"""Synapse Voice — entry point."""
from __future__ import annotations

import signal
import sys
import traceback
from datetime import datetime, timezone

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox

from . import __version__
from .config import Config
from .hotkey import GlobalHotkey
from .recorder import Recorder
from .target_lock import WindowTarget, capture_active_window, paste_into
from .transcriber import TranscriberError, get_transcriber
from .ui.bubble import Bubble
from .ui.history import HistoryDialog
from .ui.settings import SettingsDialog
from .ui.tray import Tray


class _PrewarmWorker(QObject):
    """Lazily load the faster-whisper model on a worker thread at startup
    so the first hotkey press doesn't pay the model-load cost."""

    finished = pyqtSignal()

    def __init__(self, config: Config) -> None:
        super().__init__()
        self._config = config

    def run(self) -> None:
        try:
            transcriber = get_transcriber("local", self._config)
            # Touch the loader without doing actual work
            if hasattr(transcriber, "_load"):
                transcriber._load()
        except Exception as e:
            print(f"[prewarm] skipped: {e}", flush=True)
        self.finished.emit()


class TranscribeWorker(QObject):
    finished = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, audio, mode: str, config: Config) -> None:
        super().__init__()
        self._audio = audio
        self._mode = mode
        self._config = config

    def run(self) -> None:
        try:
            transcriber = get_transcriber(self._mode, self._config)
            text = transcriber.transcribe(self._audio, language=self._config.language)
            self.finished.emit(text)
        except TranscriberError as e:
            self.failed.emit(str(e))
        except Exception as e:  # surface unexpected backend errors instead of crashing
            self.failed.emit(f"{type(e).__name__}: {e}")


class SynapseVoiceApp(QObject):
    request_toggle = pyqtSignal()  # marshals hotkey thread → Qt main thread

    def __init__(self) -> None:
        super().__init__()
        self.config = Config.load()
        self.recorder = Recorder()
        self.target: WindowTarget | None = None
        self._thread: QThread | None = None
        self._worker: TranscribeWorker | None = None

        self.bubble = Bubble()
        self.bubble.set_level_provider(lambda: self.recorder.level)
        self.tray = Tray(
            on_toggle_record=self.toggle_record,
            on_open_settings=self.open_settings,
            on_open_history=self.open_history,
            on_change_mode=self.change_mode,
            on_quit=self.quit,
            current_mode=self.config.mode,
        )
        self.tray.show()

        self.hotkey = GlobalHotkey(self.config.hotkey, self.request_toggle.emit)
        self.request_toggle.connect(self.toggle_record)
        self.hotkey.start()

        self.tray.showMessage(
            "Synapse Voice",
            f"Active. Hotkey: {self.config.hotkey} · Mode: {self.config.mode}",
            msecs=3000,
        )

        # Pre-warm local model in background — first hotkey press won't pay cold-start
        if self.config.mode == "local":
            self._prewarm_thread: QThread | None = QThread()
            self._prewarm_worker = _PrewarmWorker(self.config)
            self._prewarm_worker.moveToThread(self._prewarm_thread)
            self._prewarm_thread.started.connect(self._prewarm_worker.run)
            self._prewarm_worker.finished.connect(self._prewarm_thread.quit)
            self._prewarm_thread.finished.connect(self._prewarm_worker.deleteLater)
            self._prewarm_thread.finished.connect(self._prewarm_thread.deleteLater)
            self._prewarm_thread.start()

    def toggle_record(self) -> None:
        if not self.recorder.is_recording:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self) -> None:
        self.target = capture_active_window() if self.config.target_lock else None
        try:
            self.recorder.start()
        except Exception as e:
            self._show_error(f"Mic error: {e}")
            return
        title = self.target.title if self.target else "no target"
        self.tray.set_state("recording", f"recording → {title}")
        if self.config.show_bubble:
            self.bubble.show_state("recording", f"● Rec → {title[:32]}")

    def _stop_recording(self) -> None:
        audio = self.recorder.stop()
        if audio.size == 0:
            self.tray.set_state("idle", "idle")
            self.bubble.show_state("error", "no audio captured", auto_hide_ms=1500)
            return
        self.tray.set_state("transcribing", f"transcribing ({self.config.mode})")
        if self.config.show_bubble:
            self.bubble.show_state("transcribing", f"… transcribing ({self.config.mode})")
        self._run_transcribe(audio)

    def _run_transcribe(self, audio) -> None:
        self._thread = QThread()
        self._worker = TranscribeWorker(audio, self.config.mode, self.config)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_transcribe_done)
        self._worker.failed.connect(self._on_transcribe_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _cleanup_thread(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None

    def _on_transcribe_done(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            self.tray.set_state("idle", "idle (empty)")
            self.bubble.show_state("error", "empty transcription", auto_hide_ms=1500)
            return
        ok, mode = (False, "none")
        if self.config.autopaste:
            ok, mode = paste_into(self.target, text)
        if not self.config.autopaste:
            from .target_lock import set_clipboard
            set_clipboard(text)
            mode = "clipboard"
            ok = True

        self._record_history(text, mode)
        title = self.target.title if self.target else ""
        if mode == "pasted":
            self.bubble.show_state("done", f"✓ pasted → {title[:32]}", auto_hide_ms=1800)
            self.tray.set_state("done", f"pasted → {title[:32]}")
        elif mode == "clipboard":
            self.bubble.show_state("done", "✓ copied to clipboard", auto_hide_ms=1800)
            self.tray.set_state("done", "copied to clipboard")
        else:
            self.bubble.show_state("error", "paste failed", auto_hide_ms=1800)
            self.tray.set_state("error", "paste failed")
        QTimer.singleShot(2500, lambda: self.tray.set_state("idle", "idle"))

    def _on_transcribe_failed(self, message: str) -> None:
        self._show_error(message)

    def _show_error(self, message: str) -> None:
        self.tray.set_state("error", "error")
        self.bubble.show_state("error", f"⚠ {message[:60]}", auto_hide_ms=4000)
        self.tray.showMessage("Synapse Voice — error", message, msecs=4000)
        QTimer.singleShot(3000, lambda: self.tray.set_state("idle", "idle"))

    def _record_history(self, text: str, mode: str) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "text": text,
            "mode": self.config.mode,
            "paste_mode": mode,
            "target": self.target.title if self.target else None,
        }
        self.config.history.append(entry)
        self.config.history = self.config.history[-self.config.history_size :]
        self.config.save()

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.config)
        if dlg.exec():
            old_hotkey = self.config.hotkey
            dlg.apply_to(self.config)
            if self.config.hotkey != old_hotkey:
                self.hotkey.update(self.config.hotkey)
            self.tray.set_mode(self.config.mode)
            self.tray.showMessage(
                "Synapse Voice",
                f"Updated. Hotkey: {self.config.hotkey} · Mode: {self.config.mode}",
                msecs=2500,
            )

    def open_history(self) -> None:
        def repaste(text: str) -> None:
            from .target_lock import set_clipboard
            set_clipboard(text)
            self.tray.showMessage(
                "Synapse Voice", "History entry copied to clipboard", msecs=1500
            )

        dlg = HistoryDialog(self.config, on_repaste=repaste)
        dlg.exec()

    def change_mode(self, mode: str) -> None:
        self.config.mode = mode
        self.config.save()
        self.tray.set_mode(mode)
        self.tray.showMessage("Synapse Voice", f"Mode: {mode}", msecs=1500)

    def quit(self) -> None:
        self.hotkey.stop()
        if self.recorder.is_recording:
            self.recorder.stop()
        QApplication.instance().quit()


def _setup_logging() -> "Path":
    """File logger so silent crashes on Windows GUI builds are diagnosable."""
    from pathlib import Path

    if sys.platform == "win32":
        log_dir = Path.home() / "AppData" / "Local" / "synapse-voice" / "logs"
    else:
        log_dir = Path.home() / ".local" / "share" / "synapse-voice" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "synapse-voice.log"

    def _excepthook(exctype, value, tb):
        ts = datetime.now(timezone.utc).isoformat()
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n=== {ts} ===\n")
                traceback.print_exception(exctype, value, tb, file=f)
        except Exception:
            pass
        traceback.print_exception(exctype, value, tb)

    sys.excepthook = _excepthook
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(
                f"\n[{datetime.now(timezone.utc).isoformat()}] "
                f"synapse-voice {__version__} starting on {sys.platform}\n"
            )
    except Exception:
        pass
    return log_file


def main() -> int:
    log_file = _setup_logging()

    try:
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)
        app.setApplicationName("Synapse Voice")
        app.setApplicationVersion(__version__)

        if not Tray.isSystemTrayAvailable():
            QMessageBox.critical(
                None,
                "Synapse Voice",
                "System tray is not available on this desktop. Aborting.",
            )
            return 1

        signal.signal(signal.SIGINT, signal.SIG_DFL)

        sv = SynapseVoiceApp()
        return app.exec()
    except Exception:
        # Last-resort: log + show error dialog so Windows users see *something*
        import traceback as _tb

        err = _tb.format_exc()
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n=== fatal: {datetime.now(timezone.utc).isoformat()} ===\n{err}\n")
        except Exception:
            pass
        try:
            from PyQt6.QtWidgets import QApplication as _QA, QMessageBox as _QM

            if _QA.instance() is None:
                _QA(sys.argv)
            _QM.critical(None, "Synapse Voice — fatal error", f"{err}\n\nLog: {log_file}")
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    sys.exit(main())
