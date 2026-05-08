"""Synapse Voice — entry point."""
from __future__ import annotations

import signal
import sys
import traceback
from datetime import datetime, timezone
from typing import Optional

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox

from . import __version__
from .config import Config
from .hotkey import GlobalHotkey
from .logger import get as _get_logger, init_logging, log_file_path
from .recorder import Recorder
from .target_lock import WindowTarget, capture_active_window, paste_into
from .transcriber import TranscriberError, get_transcriber
from .ui.bubble import Bubble
from .ui.orb_overlay import OrbOverlay
from .ui.history import HistoryDialog
from .ui.main_window import MainWindow
from .ui.settings import SettingsDialog
from .ui.tray import Tray

_log = _get_logger(__name__)


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
            # AI cleanup-layer (best-effort, never blocks the result).
            if text and self._config.cleanup_enabled:
                from .cleanup_client import cleanup_text

                cleaned = cleanup_text(
                    text,
                    transcribe_endpoint=self._config.subunit_endpoint,
                    api_key=self._config.subunit_api_key,
                    style=self._config.cleanup_style or "tidy",
                )
                if cleaned and cleaned.strip() != text.strip():
                    _log.info(
                        "Cleanup applied (style=%s, %d→%d chars)",
                        self._config.cleanup_style,
                        len(text),
                        len(cleaned),
                    )
                    text = cleaned
            self.finished.emit(text)
        except TranscriberError as e:
            _log.error("Transcribe failed (mode=%s): %s", self._mode, e)
            self.failed.emit(str(e))
        except Exception as e:  # surface unexpected backend errors instead of crashing
            _log.exception("Unexpected transcribe error (mode=%s)", self._mode)
            self.failed.emit(f"{type(e).__name__}: {e}")


class SynapseVoiceApp(QObject):
    request_toggle = pyqtSignal()  # marshals hotkey thread → Qt main thread (toggle mode)
    request_start = pyqtSignal()   # hold-mode press
    request_stop = pyqtSignal()    # hold-mode release

    def __init__(self) -> None:
        super().__init__()
        self.config = Config.load()
        self.recorder = Recorder()
        self.target: WindowTarget | None = None
        self._active_threads: list[tuple[QThread, "TranscribeWorker"]] = []
        self._last_audio_seconds: float = 0.0
        self._last_toggle_at: float = 0.0

        self.bubble = Bubble()
        self.bubble.set_level_provider(lambda: self.recorder.level)
        # v0.4: Orb is the new persistent overlay. Created always but only
        # shown when the user opts in via Settings — keeps the door open
        # for in-session toggling without a restart.
        self.orb: Optional[OrbOverlay] = None
        if self.config.use_orb_overlay:
            self.orb = OrbOverlay(self.config, on_change_mode=self.change_mode)
            self.orb.set_level_provider(lambda: self.recorder.level)
            self.orb.show()
        self.main_window = MainWindow(
            config=self.config,
            on_change_mode=self.change_mode,
            on_open_settings=self.open_settings,
            on_open_history=self.open_history,
            on_quit=self.quit,
        )
        self.tray = Tray(
            on_toggle_record=self.toggle_record,
            on_open_settings=self.open_settings,
            on_open_history=self.open_history,
            on_open_window=self.open_window,
            on_change_mode=self.change_mode,
            on_quit=self.quit,
            current_mode=self.config.mode,
        )
        self.tray.show()

        # In "toggle" mode the hotkey toggles record on each press. In "hold"
        # mode pressing starts recording and releasing stops it.
        self.hotkey = GlobalHotkey(
            self.config.hotkey,
            on_trigger=self._on_hotkey_press,
            mode=self.config.recording_mode,
            on_release=self._on_hotkey_release,
        )
        self.request_toggle.connect(self.toggle_record)
        self.request_start.connect(self._start_recording)
        self.request_stop.connect(self._stop_recording)
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

        # Auto-update check (delayed so app startup isn't blocked by network).
        if self.config.auto_update_check:
            QTimer.singleShot(8_000, self._check_for_updates)

    def _on_hotkey_press(self) -> None:
        """Called from the pynput thread on hotkey press."""
        if self.config.recording_mode == "hold":
            self.request_start.emit()
        else:
            self.request_toggle.emit()

    def _on_hotkey_release(self) -> None:
        """Called from the pynput thread on hotkey release (hold mode only)."""
        if self.config.recording_mode == "hold":
            self.request_stop.emit()

    def toggle_record(self) -> None:
        # Debounce — pynput occasionally double-fires a hotkey on Windows when
        # the user releases the modifier slightly before the trigger key, and
        # any double-toggle within 250ms is almost certainly noise rather
        # than intent.
        import time as _time

        now = _time.monotonic()
        if now - self._last_toggle_at < 0.25:
            return
        self._last_toggle_at = now

        if not self.recorder.is_recording:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self) -> None:
        # Pre-flight: if the selected mode needs credentials we don't have,
        # prompt the user to fill them in instead of recording 30s of audio
        # and only then surfacing "API key missing".
        from .transcriber.base import preflight_check

        missing = preflight_check(self.config.mode, self.config)
        if missing:
            self._prompt_for_credentials(missing)
            return

        self.target = capture_active_window() if self.config.target_lock else None
        try:
            self.recorder.start()
            _log.info("Recording started (target=%s)", self.target.title if self.target else None)
        except Exception as e:
            _log.exception("Mic error on start")
            self._show_error(f"Mic error: {e}")
            return
        title = self.target.title if self.target else "no target"
        self.tray.set_state("recording", f"recording → {title}")
        self._safe_status("recording", color="#ff585c")
        # Bubble + Orb are mutually exclusive — orb wins when enabled.
        if self.config.show_bubble and self.orb is None:
            self.bubble.show_state("recording", f"● Rec → {title[:32]}")
        if self.orb is not None:
            self.orb.show_state("recording")

    def _stop_recording(self) -> None:
        audio = self.recorder.stop()
        if audio.size == 0:
            self.tray.set_state("idle", "idle")
            self._safe_status("idle")
            if self.orb is None:
                self.bubble.show_state("error", "no audio captured", auto_hide_ms=2500)
            else:
                self.orb.show_state("error")
            return
        self._last_audio_seconds = float(audio.size) / float(self.recorder.sample_rate)
        self.tray.set_state("transcribing", f"transcribing ({self.config.mode})")
        self._safe_status("transcribing", color="#40d6ff")
        if self.config.show_bubble and self.orb is None:
            self.bubble.show_state("transcribing", f"… transcribing ({self.config.mode})")
        if self.orb is not None:
            self.orb.show_state("transcribing")
        self._run_transcribe(audio)

    def _run_transcribe(self, audio) -> None:
        # Capture thread+worker locally so a follow-up call does not orphan the
        # previous pair (otherwise rapid hotkey re-trigger could call
        # deleteLater on the new thread while the old one's finished signal
        # fires).
        thread = QThread()
        worker = TranscribeWorker(audio, self.config.mode, self.config)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_transcribe_done)
        worker.failed.connect(self._on_transcribe_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        # Keep references alive until cleanup
        self._active_threads.append((thread, worker))
        thread.finished.connect(lambda t=thread, w=worker: self._drop_thread(t, w))
        thread.start()

    def _drop_thread(self, thread, worker) -> None:
        try:
            self._active_threads.remove((thread, worker))
        except ValueError:
            pass

    def _on_transcribe_done(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            self.tray.set_state("idle", "idle (empty)")
            if self.orb is None:
                self.bubble.show_state("error", "empty transcription", auto_hide_ms=2500)
            else:
                self.orb.show_state("error")
            return
        if self.config.autopaste:
            # Hide the bubble BEFORE paste so it doesn't steal focus from the
            # target window. The Tool + WA_ShowWithoutActivating flags help on
            # Linux but not always on Windows 11 — there the bubble being on
            # top can still interfere with our SetForegroundWindow + Ctrl+V.
            if self.config.show_bubble and self.bubble.isVisible():
                self.bubble.hide()
            _ok, mode = paste_into(self.target, text)
        else:
            from .target_lock import set_clipboard

            set_clipboard(text)
            mode = "clipboard"

        self._record_history(text, mode)
        title = self.target.title if self.target else ""
        if mode == "pasted":
            if self.orb is None:
                self.bubble.show_state("done", f"✓ pasted → {title[:32]}", auto_hide_ms=2800)
            else:
                self.orb.show_state("done")
            self.tray.set_state("done", f"pasted → {title[:32]}")
        elif mode == "clipboard":
            if self.orb is None:
                self.bubble.show_state("done", "✓ copied to clipboard", auto_hide_ms=2800)
            else:
                self.orb.show_state("done")
            self.tray.set_state("done", "copied to clipboard")
        else:
            if self.orb is None:
                self.bubble.show_state("error", "paste failed", auto_hide_ms=2800)
            else:
                self.orb.show_state("error")
            self.tray.set_state("error", "paste failed")
        QTimer.singleShot(2500, lambda: (self.tray.set_state("idle", "idle"), self._safe_status("idle")))

    def _on_transcribe_failed(self, message: str) -> None:
        # Auth / credentials problems are user-fixable in Settings — surface a
        # dialog instead of just a tray flash that the user can't act on.
        lower = message.lower()
        is_auth = any(
            kw in lower
            for kw in ("api key", "401", "invalid api", "unauthor", "forbidden", "403")
        )
        if is_auth:
            self.tray.set_state("idle", "idle")
            self._safe_status("idle")
            self.bubble.fade_out()
            self._prompt_for_credentials(
                f"{self.config.mode.title()} authentication failed.\n\n"
                f"{message[:200]}\n\nOpen Settings to update credentials?"
            )
            return
        self._show_error(message)

    def _show_error(self, message: str) -> None:
        _log.error("UI error surfaced: %s", message)
        self.tray.set_state("error", "error")
        self._safe_status("error", color="#ffc450")
        self.bubble.show_state("error", f"⚠ {message[:60]}", auto_hide_ms=5000)
        self.tray.showMessage("Synapse Voice — error", message, msecs=4000)
        QTimer.singleShot(3000, lambda: (self.tray.set_state("idle", "idle"), self._safe_status("idle")))

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
        self.config.total_transcriptions += 1
        if self._last_audio_seconds:
            self.config.total_audio_seconds += self._last_audio_seconds
        self.config.save()
        try:
            self.main_window.refresh()
        except Exception:
            pass

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.config)
        if dlg.exec():
            old_hotkey = self.config.hotkey
            dlg.apply_to(self.config)
            # Settings can change credentials/endpoint/model — drop the cached
            # transcriber instances so the next call uses the new values.
            from .transcriber import clear_cache as _clear_transcriber_cache

            _clear_transcriber_cache()
            if self.config.hotkey != old_hotkey:
                self.hotkey.update(self.config.hotkey)
            self.tray.set_mode(self.config.mode)
            try:
                self.main_window.refresh_mode()
            except Exception:
                pass
            self.tray.showMessage(
                "Synapse Voice",
                f"Updated. Hotkey: {self.config.hotkey} · Mode: {self.config.mode}",
                msecs=2500,
            )

    def _prompt_for_credentials(self, message: str) -> None:
        from PyQt6.QtWidgets import QMessageBox

        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Synapse Voice")
        box.setText(message)
        box.setStandardButtons(
            QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Cancel
        )
        if box.exec() == QMessageBox.StandardButton.Open:
            self.open_settings()

    def _safe_status(self, label: str, color: str = None) -> None:
        try:
            if color:
                self.main_window.set_status(label, color=color)
            else:
                self.main_window.set_status(label)
        except Exception:
            pass

    def open_window(self) -> None:
        self.main_window.show()
        self.main_window.raise_()
        self.main_window.activateWindow()
        self.main_window.refresh()

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

    def _check_for_updates(self) -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtWidgets import QMessageBox

        from . import updater

        info = updater.check()
        if info is None or not info.available:
            return
        _log.info("Prompting user for update %s → %s", info.current, info.latest)
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Synapse Voice — Update available")
        # If we have a direct installer URL we offer a one-click install,
        # otherwise we fall back to the release page (e.g. unsupported
        # platform or asset naming changed).
        if info.installer_url:
            box.setText(
                f"A new version is available:\n\n"
                f"  Current: v{info.current}\n"
                f"  Latest:  {info.latest}\n\n"
                f"Download and install now? The app will close + reopen."
            )
            box.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            box.setDefaultButton(QMessageBox.StandardButton.Yes)
            if box.exec() == QMessageBox.StandardButton.Yes:
                self._download_and_install(info)
        else:
            box.setText(
                f"A new version is available:\n\n"
                f"  Current: v{info.current}\n"
                f"  Latest:  {info.latest}\n\n"
                f"Open the release page to download?"
            )
            box.setStandardButtons(
                QMessageBox.StandardButton.Open
                | QMessageBox.StandardButton.Ignore
            )
            if box.exec() == QMessageBox.StandardButton.Open:
                QDesktopServices.openUrl(QUrl(info.release_url))

    def _download_and_install(self, info) -> None:
        """Download the platform installer in a worker thread with a
        progress dialog, then spawn the installer + quit ourselves so it
        can replace files."""
        from pathlib import Path

        from PyQt6.QtCore import Qt, QThread, pyqtSignal
        from PyQt6.QtWidgets import QMessageBox, QProgressDialog

        from . import updater

        progress = QProgressDialog(
            f"Downloading {info.installer_name}…",
            "Cancel",
            0,
            100,
        )
        progress.setWindowTitle("Synapse Voice — Updating")
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)

        class _Worker(QThread):
            progress_changed = pyqtSignal(int)
            finished_with_path = pyqtSignal(str)
            failed = pyqtSignal(str)

            def __init__(self, url: str) -> None:
                super().__init__()
                self.url = url
                self._cancelled = False

            def cancel(self) -> None:
                self._cancelled = True

            def run(self) -> None:
                try:
                    def cb(d: int, t: int) -> None:
                        if self._cancelled:
                            raise RuntimeError("cancelled")
                        if t:
                            self.progress_changed.emit(int(100 * d / t))
                    target = updater.download_installer(self.url, progress_cb=cb)
                    self.finished_with_path.emit(str(target))
                except Exception as e:
                    if not self._cancelled:
                        self.failed.emit(str(e))

        worker = _Worker(info.installer_url)
        # Keep ref so it isn't GC'd mid-flight.
        self._update_worker = worker

        worker.progress_changed.connect(progress.setValue)

        def on_ok(p: str) -> None:
            progress.close()
            try:
                updater.launch_installer_and_quit(Path(p))
            except Exception as e:
                _log.exception("launch_installer failed")
                QMessageBox.warning(
                    None,
                    "Synapse Voice — Update failed",
                    f"Could not launch the installer:\n\n{e}",
                )
                return
            # Give the installer a beat to actually start before we exit
            # — otherwise some shells reap it as our child.
            QApplication.instance().processEvents()
            self.quit()

        def on_fail(err: str) -> None:
            progress.close()
            _log.error("Update download failed: %s", err)
            QMessageBox.warning(
                None,
                "Synapse Voice — Update failed",
                f"Could not download the update:\n\n{err}\n\n"
                f"You can grab it manually from:\n{info.release_url}",
            )

        worker.finished_with_path.connect(on_ok)
        worker.failed.connect(on_fail)
        progress.canceled.connect(worker.cancel)
        worker.start()

    def quit(self) -> None:
        self.hotkey.stop()
        if self.recorder.is_recording:
            self.recorder.stop()
        QApplication.instance().quit()


def main() -> int:
    log_file = init_logging(__version__)

    try:
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)
        app.setApplicationName("Synapse Voice")
        app.setApplicationVersion(__version__)

        # Brand icon — used for the title bar, Alt-Tab switcher and Win taskbar.
        try:
            from PyQt6.QtGui import QIcon
            from .ui.widgets import make_logo_pixmap

            app_icon = QIcon(make_logo_pixmap(size=256))
            app.setWindowIcon(app_icon)
        except Exception:
            pass

        # Windows: register an explicit AppUserModelID so taskbar entries
        # group under "Synapse Voice" instead of the generic Python interp.
        if sys.platform == "win32":
            try:
                import ctypes

                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "subunit.synapse-voice"
                )
            except Exception:
                pass

        if not Tray.isSystemTrayAvailable():
            QMessageBox.critical(
                None,
                "Synapse Voice",
                "System tray is not available on this desktop. Aborting.",
            )
            return 1

        signal.signal(signal.SIGINT, signal.SIG_DFL)

        sv = SynapseVoiceApp()
        _log.info("App ready (mode=%s, hotkey=%s)", sv.config.mode, sv.config.hotkey)
        return app.exec()
    except Exception:
        _log.exception("Fatal error during app startup")
        try:
            from PyQt6.QtWidgets import QApplication as _QA, QMessageBox as _QM

            if _QA.instance() is None:
                _QA(sys.argv)
            err = traceback.format_exc()
            _QM.critical(
                None,
                "Synapse Voice — fatal error",
                f"{err}\n\nLog: {log_file}",
            )
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    sys.exit(main())
