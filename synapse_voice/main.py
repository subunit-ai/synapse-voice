"""Sonar — entry point."""
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


def _resolve_mic_device(name: str) -> Optional[int]:
    """Map a saved mic-device name to a sounddevice index. Returns None if
    the user hasn't picked one or the saved name no longer exists (we
    silently fall back to the system default in that case)."""
    if not name:
        return None
    try:
        from .recorder import list_input_devices

        for d in list_input_devices():
            if d["name"] == name:
                return d["index"]
    except Exception:
        pass
    _log.warning("Saved mic device %r not found — using system default", name)
    return None


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
    # v0.9.11: emit (text, effective_quality_mode). The second arg is "" for
    # local transcripts and "instant"/"fast"/"quality" for cloud — it's the
    # tier the server actually ran (may differ from the user's selection
    # when quality_mode="auto"). UI uses it to render a per-row tier badge
    # in Recent-Transcripts.
    finished = pyqtSignal(str, str)
    failed = pyqtSignal(str)
    # v0.3.25: emit (style, label) when Auto-Mode picks a style different
    # from the user's manual default. main.py uses this to flash a small
    # tray confirmation so the user knows what got applied.
    auto_mode_picked = pyqtSignal(str, str)

    def __init__(
        self,
        audio,
        mode: str,
        config: Config,
        window_title: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._audio = audio
        self._mode = mode
        self._config = config
        self._window_title = window_title or ""

    def run(self) -> None:
        try:
            transcriber = get_transcriber(self._mode, self._config)
            text = transcriber.transcribe(self._audio, language=self._config.language)
            # Style is also used by the Meetings persistence below; initialise
            # it before the cleanup block so it is always defined.
            style = self._config.cleanup_style or "prompt"
            # AI cleanup-layer (best-effort, never blocks the result).
            if text and self._config.cleanup_enabled:
                from .cleanup_client import cleanup_text

                # v0.3.25: Auto-Mode — derive style from the active
                # window if enabled. Falls back to the user's manual
                # cleanup_style if no rule matched.
                if self._config.cleanup_auto_mode and self._window_title:
                    from . import auto_mode

                    detection = auto_mode.detect(self._window_title)
                    detection = auto_mode.apply_overrides(
                        detection,
                        self._config.auto_mode_overrides or {},
                        self._window_title,
                    )
                    if detection is not None:
                        style, label = detection
                        if style != (self._config.cleanup_style or "prompt"):
                            self.auto_mode_picked.emit(style, label)
                        _log.info(
                            "Auto-Mode picked '%s' for window '%s' (label=%s)",
                            style,
                            self._window_title[:40],
                            label,
                        )

                # v0.6.0 (read.ai-inspired long-form mode): if the
                # recording is longer than the configured threshold,
                # override the cleanup style with the long-form style.
                # Default: > 60 seconds → "summary".  Set the threshold
                # to 0 in Settings to disable this auto-switch.
                threshold = max(0, getattr(self._config, "long_form_threshold_seconds", 60) or 0)
                if threshold > 0 and self._audio is not None:
                    # _audio is mono float32 at 16 kHz (see recorder.py).
                    duration_s = float(self._audio.shape[0]) / 16000.0
                    if duration_s >= threshold:
                        long_style = (
                            getattr(self._config, "long_form_cleanup_style", "summary")
                            or "summary"
                        )
                        _log.info(
                            "Long-form mode: %.1fs >= %ds → switching style "
                            "%r → %r",
                            duration_s, threshold, style, long_style,
                        )
                        style = long_style

                cleaned = cleanup_text(
                    text,
                    transcribe_endpoint=self._config.subunit_endpoint,
                    api_key=self._config.subunit_api_key,
                    style=style,
                )
                if cleaned and cleaned.strip() != text.strip():
                    _log.info(
                        "Cleanup applied (style=%s, %d→%d chars)",
                        style,
                        len(text),
                        len(cleaned),
                    )
                    text = cleaned
            # v0.9.12 DACH Formatting Pack: post-process pass that fixes
            # currency phrases / abbreviation spacing / German quotes.
            # Runs BEFORE vocab so the vocab replacements see the
            # canonical spacing, and AFTER cleanup so any rewrite the
            # cleanup model did is normalised.
            if text and getattr(self._config, "dach_format_enabled", False):
                from .dach_format import format_dach

                text = format_dach(text)
            # v0.3.9 Lexikon: post-process literal-replace pass to catch
            # mishears the prompt didn't fix (Whisper sometimes ignores
            # initial_prompt for non-canonical pronunciations).
            if text and self._config.vocabulary:
                from .transcriber.base import apply_vocab_replace

                text = apply_vocab_replace(text, self._config)
            # v0.3.29 Subunit Suite — Voice → Synapse bridge.  Best-effort,
            # never blocks the paste path.  Runs after vocab so the saved
            # version matches what gets pasted.
            if (
                text
                and self._config.synapse_save_enabled
                and self._config.subunit_api_key
            ):
                self._save_to_synapse(text)
            # v0.7.0 — persist long-form recordings as Meetings so they show
            # up in the Meetings tab and can be revisited / acted on.
            if text and self._audio is not None:
                duration_s = float(self._audio.shape[0]) / 16000.0
                self._maybe_persist_meeting(text=text, duration_s=duration_s, style_used=style)
            # v0.9.11: capture the effective tier the server actually ran.
            # Local transcribers don't set this — emit "" so the UI shows
            # no badge for local runs.
            effective_mode = str(getattr(transcriber, "last_quality_mode", "") or "")
            self.finished.emit(text, effective_mode)
        except TranscriberError as e:
            _log.error("Transcribe failed (mode=%s): %s", self._mode, e)
            self.failed.emit(str(e))
        except Exception as e:  # surface unexpected backend errors instead of crashing
            _log.exception("Unexpected transcribe error (mode=%s)", self._mode)
            self.failed.emit(f"{type(e).__name__}: {e}")

    def _save_to_synapse(self, text: str) -> None:
        """Best-effort POST of the final transcript to /v1/synapse/save.

        Errors are logged but never re-raised — the user's paste-flow
        is the primary path and must never wait on or be aborted by
        the Synapse bridge.
        """
        import json
        import time
        import urllib.parse
        import urllib.request

        # Derive /v1/synapse/save from the configured /v1/transcribe URL.
        endpoint = (self._config.subunit_endpoint or "").rstrip("/")
        if endpoint.endswith("/v1/transcribe"):
            save_url = endpoint[: -len("/v1/transcribe")] + "/v1/synapse/save"
        else:
            parsed = urllib.parse.urlparse(endpoint)
            base = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme else endpoint
            save_url = base.rstrip("/") + "/v1/synapse/save"

        payload = {
            "text": text,
            "window_title": (self._window_title or "")[:200],
            "cleanup_style": self._config.cleanup_style or "",
            "language": self._config.language or "",
            "transcribed_at": int(time.time()),
        }
        try:
            req = urllib.request.Request(
                save_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": self._config.subunit_api_key,
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=4) as r:
                r.read()
            _log.info("Synapse: saved transcript (%d chars)", len(text))
        except Exception as e:
            _log.warning("Synapse save failed (non-fatal): %s", e)

    def _maybe_persist_meeting(self, *, text: str, duration_s: float, style_used: str) -> None:
        """Save long-form recordings to the Meetings store.

        A recording is considered a "meeting" if its duration meets or exceeds
        ``Config.long_form_threshold_seconds``. Short dictations are not
        persisted — they live in the regular history list.

        Best-effort: any I/O error is logged and swallowed so the paste path
        is never blocked by storage problems.
        """
        try:
            # v0.9.13 (Codex P1): respect the privacy toggle. Settings
            # promises "keine Transkripte werden auf diesem Gerät
            # gespeichert" — Meetings persistence was bypassing that.
            if not bool(getattr(self._config, "history_enabled", True)):
                _log.info(
                    "Skipping meeting persist: history_enabled=False (%.1fs)",
                    duration_s,
                )
                return
            threshold = max(0, getattr(self._config, "long_form_threshold_seconds", 60) or 0)
            if threshold <= 0 or duration_s < threshold:
                return
            from .meetings import MeetingsStore, detect_source_from_window_title
            store = MeetingsStore()
            cleanup_versions: dict = {}
            # The raw transcribed text from Whisper goes into transcript_raw.
            # If the cleanup style was anything other than "raw"/"prompt", the
            # post-cleanup ``text`` is also a usable rendering — cache it under
            # the style name so the user can flip between versions without
            # re-calling the cleanup endpoint.
            if style_used and style_used not in ("raw", "prompt") and text:
                cleanup_versions[style_used] = text
            meeting = store.create(
                transcript_raw=text,
                duration_seconds=duration_s,
                language=self._config.language or "",
                source=detect_source_from_window_title(self._window_title),
                window_title=(self._window_title or "")[:200],
                cleanup_versions=cleanup_versions,
                metadata={
                    "style_used": style_used,
                    "mode": self._mode,
                },
            )
            _log.info("Meeting persisted: id=%s duration=%.1fs", meeting.id, duration_s)

            # v0.8.0 (Codex Top 1): kick off speaker diarization in a
            # background thread. The Meeting is already saved with the
            # raw transcript — diarization adds a speaker-tagged version
            # to ``cleanup_versions`` when it returns. UI will show it
            # in the style picker. Best-effort: failures are logged.
            if (
                self._config.diarization_enabled
                and self._config.subunit_api_key
                and self._audio is not None
                and self._audio.size > 0
            ):
                self._kick_off_diarization(meeting.id, self._audio.copy())
        except Exception as e:
            _log.warning("Failed to persist meeting (non-fatal): %s", e)

    def _kick_off_diarization(self, meeting_id: str, audio: "np.ndarray") -> None:
        """Run diarization on a background thread; update the Meeting on completion."""
        import threading

        def _worker() -> None:
            try:
                from .diarization_client import (
                    diarize_pcm,
                    merge_whisper_with_speakers,
                    format_speaker_transcript,
                )
                _log.info("Diarization starting for meeting %s …", meeting_id)
                segments = diarize_pcm(
                    audio,
                    sample_rate=16000,
                    transcribe_endpoint=self._config.subunit_endpoint,
                    api_key=self._config.subunit_api_key,
                    max_speakers=self._config.diarization_max_speakers,
                )
                if not segments:
                    _log.info("Diarization returned no segments for %s", meeting_id)
                    return
                # We don't have Whisper segments locally — fall back to
                # rendering pure speaker time-ranges as a "transcript".
                speakers_only = format_speaker_transcript([])  # empty without whisper
                # Rough text fallback: just list the speaker ranges. The
                # client-side merge with Whisper segments would need them
                # from the transcribe call; v0.8.0 stays simple and just
                # tags the raw transcript with speaker labels in metadata.
                from .meetings import MeetingsStore
                store = MeetingsStore()
                m = store.get(meeting_id)
                if not m:
                    return
                segments_json = [
                    {"start_s": s.start_s, "end_s": s.end_s, "speaker": s.speaker}
                    for s in segments
                ]
                m.metadata["diarize_segments"] = segments_json
                m.metadata["diarize_num_speakers"] = len({s.speaker for s in segments})
                # Build a simple speaker-tagged rendering of the raw transcript
                # by chunking the transcript into segments matching the speaker
                # time ranges' duration ratios. Best-effort.
                tagged = _approximate_speaker_transcript(m.transcript_raw, segments)
                if tagged:
                    m.cleanup_versions["speaker_transcript"] = tagged
                store.update(m)
                _log.info(
                    "Diarization done for %s: %d speakers, %d segments",
                    meeting_id, m.metadata["diarize_num_speakers"], len(segments),
                )
            except Exception as e:
                _log.warning("Diarization worker failed for %s: %s", meeting_id, e)

        t = threading.Thread(target=_worker, name=f"diarize-{meeting_id[:8]}", daemon=True)
        t.start()


def _approximate_speaker_transcript(transcript_raw: str, segments: list) -> str:
    """Heuristic: distribute the raw transcript across speaker time ranges by duration.

    Since the local client only has the joined transcript (no per-segment
    text from Whisper), we sentence-split and divide by total speech
    duration. This is imperfect but readable — good enough for v0.8.0
    until we add segment-level streaming.
    """
    import re

    if not transcript_raw or not segments:
        return ""
    # Sort speaker segments by start time
    sorted_segs = sorted(segments, key=lambda s: s.start_s)
    total_duration = sum(max(0.0, s.end_s - s.start_s) for s in sorted_segs) or 1.0

    # Split transcript into sentences (keeping punctuation).
    sentences = re.split(r"(?<=[.!?])\s+", transcript_raw.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return ""

    # Distribute sentences among speakers proportional to their cumulative
    # speech duration (very rough — sentence count ≠ word count, but the
    # output is acceptable until we wire segment-level text).
    out_lines: list[str] = []
    sentence_idx = 0
    cumulative_count = 0
    for seg in sorted_segs:
        share = (seg.end_s - seg.start_s) / total_duration
        chunk_size = max(1, round(share * len(sentences)))
        chunk = sentences[sentence_idx : sentence_idx + chunk_size]
        if chunk:
            out_lines.append(f"{seg.speaker}: " + " ".join(chunk))
            sentence_idx += chunk_size
            cumulative_count += chunk_size
        if sentence_idx >= len(sentences):
            break

    # Append any leftover sentences to the last speaker
    if sentence_idx < len(sentences) and out_lines:
        last = out_lines[-1]
        out_lines[-1] = last + " " + " ".join(sentences[sentence_idx:])

    return "\n\n".join(out_lines)


class SynapseVoiceApp(QObject):
    request_toggle = pyqtSignal()  # marshals hotkey thread → Qt main thread (toggle mode)
    request_start = pyqtSignal()   # hold-mode press
    request_stop = pyqtSignal()    # hold-mode release

    def __init__(self) -> None:
        super().__init__()
        self.config = Config.load()
        # v0.3.19: prime the i18n bundle from config before any UI is
        # built so first-render strings reflect the user's language.
        from . import i18n
        i18n.set_language(self.config.ui_language or "de")
        # v0.3.21: apply theme palette before any widget paints. Theme is
        # an app-level concern so we set it on the QApplication once here
        # (and again on Settings-save if the user toggles it).
        from . import theme
        theme.apply(QApplication.instance(), self.config.ui_theme or "dark")
        self.recorder = Recorder(device=_resolve_mic_device(self.config.mic_device_name))
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
        # v0.9.10: cached update-info from the last GitHub check (set by
        # _check_for_updates). Profile-card UI reads this to decide
        # whether to show "Update verfügbar" pill + install button.
        self._available_update = None
        # v0.9.13 (Codex P1): track whether the LAST tray balloon shown
        # was the update toast. Without this guard, _on_tray_balloon_clicked
        # opens the installer dialog for ANY balloon click while an
        # update is cached — including the "paste ready" / status
        # toasts the user is just trying to dismiss.
        self._last_tray_balloon_was_update = False
        if self.config.use_orb_overlay:
            self.orb = OrbOverlay(self.config, on_change_mode=self.change_mode)
            self.orb.set_level_provider(lambda: self.recorder.level)
            # v0.9.17: respect auto-hide — start invisible, surface only on recording.
            if not getattr(self.config, "orb_overlay_auto_hide", False):
                self.orb.show()
        self.main_window = MainWindow(
            config=self.config,
            on_change_mode=self.change_mode,
            on_open_settings=self.open_settings,
            on_open_history=self.open_history,
            on_open_meetings=self.open_meetings,
            on_start_meeting=self.start_meeting,
            on_quit=self.quit,
        )
        self.tray = Tray(
            on_toggle_record=self.toggle_record,
            on_open_settings=self.open_settings,
            on_open_history=self.open_history,
            on_open_meetings=self.open_meetings,
            on_open_window=self.open_window,
            on_change_mode=self.change_mode,
            on_quit=self.quit,
            current_mode=self.config.mode,
        )
        self.tray.show()
        # v0.9.10: clicking the tray balloon (when the update toast fires)
        # opens the full install dialog. messageClicked is a QSystemTrayIcon
        # signal; we route it to the install prompt only when an update is
        # actually pending — other tray notifications (paste-done etc.)
        # leave it a no-op.
        try:
            self.tray.messageClicked.connect(self._on_tray_balloon_clicked)
        except Exception:
            pass

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
            "Sonar",
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

        # v0.3.22: pull plan + trial state from the server at boot — that's
        # the source of truth, the local config is just a cache. Delayed
        # so the UI is up first and a slow network doesn't block startup.
        QTimer.singleShot(2_000, self._refresh_account_info)

        # v0.4: Show the 4-step onboarding wizard on first launch. We delay
        # by 600ms so the tray icon + main window are settled by the time
        # the modal pops up — looks less janky than firing during ctor.
        if not self.config.has_seen_onboarding:
            QTimer.singleShot(600, self._show_onboarding)

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
        if self.config.sound_enabled:
            from . import sounds
            sounds.play("start", volume=self.config.sound_volume)

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
        # Pass the captured window title so Auto-Mode can derive the
        # cleanup style. We use the title from `target_lock.capture_active_window`
        # taken at recording-start, NOT at paste-time, because the user
        # may switch windows briefly while speaking — we want the style
        # to match where they were when they started.
        win_title = self.target.title if self.target else None
        worker = TranscribeWorker(
            audio, self.config.mode, self.config, window_title=win_title
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_transcribe_done)
        worker.failed.connect(self._on_transcribe_failed)
        worker.auto_mode_picked.connect(self._on_auto_mode_picked)
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

    def _on_transcribe_done(self, text: str, quality_mode: str = "") -> None:
        text = (text or "").strip()
        if not text:
            # v0.9.15: empty transcription is the #1 silent-fail mode the
            # user perceives as "nothing happened". Server-side VAD filter
            # rejects clips below the speech threshold. Surface a real
            # Windows tray balloon (not just the orb flash, which Erik on
            # Win-ARM tablet doesn't always notice) AND log the duration.
            duration_s = float(getattr(self, "_last_audio_seconds", 0.0) or 0.0)
            _log.warning(
                "Empty transcription returned (mode=%s, duration=%.1fs). "
                "Common causes: mic level too low, audio shorter than VAD "
                "threshold, background-only clip.",
                self.config.mode, duration_s,
            )
            self.tray.set_state("idle", "idle (empty)")
            if self.orb is None:
                self.bubble.show_state(
                    "error",
                    "Keine Sprache erkannt — Mic-Level prüfen",
                    auto_hide_ms=3500,
                )
            else:
                self.orb.show_state("error")
            try:
                self.tray.showMessage(
                    "Sonar — keine Sprache erkannt",
                    f"Aufnahme war leer ({duration_s:.1f}s). Mikrofon-Level "
                    "und Eingabegerät in Settings prüfen.",
                    msecs=4500,
                )
                self._last_tray_balloon_was_update = False
            except Exception:
                pass
            return
        if self.config.autopaste:
            # Hide the bubble BEFORE paste so it doesn't steal focus from the
            # target window. The Tool + WA_ShowWithoutActivating flags help on
            # Linux but not always on Windows 11 — there the bubble being on
            # top can still interfere with our SetForegroundWindow + Ctrl+V.
            if self.config.show_bubble and self.bubble.isVisible():
                self.bubble.hide()
            # v0.5.7: clipboard-restore disabled.  The previous behavior
            # was: save user's clipboard before paste, restore 2.5s after
            # a "successful" paste.  Codex found that "successful" was
            # being claimed too generously (keybd_event always returns
            # True, SendInput only confirms queueing, WM_PASTE only
            # confirms delivery), so on Win-ARM and edge cases the
            # transcript got wiped before the user could re-paste
            # manually.  Better behavior: transcript stays in clipboard
            # so the user can always paste it manually.  Re-introduce
            # restore in v0.5.8+ once paste-success is verifiable via
            # foreground-window matching post-paste.
            _ok, mode = paste_into(self.target, text)
        else:
            from .target_lock import set_clipboard

            set_clipboard(text)
            mode = "clipboard"

        self._record_history(text, mode, quality_mode=quality_mode)
        # TJ-feedback v0.3.17: sound ONLY on hotkey-press (record start),
        # not on paste-done. The "done" event is already conveyed by the
        # text appearing in the target window — a second sound felt
        # noisy on rapid back-to-back dictations.
        title = self.target.title if self.target else ""
        if mode == "pasted":
            if self.orb is None:
                self.bubble.show_state("done", f"✓ pasted → {title[:32]}", auto_hide_ms=2800)
            else:
                self.orb.show_state("done")
            self.tray.set_state("done", f"pasted → {title[:32]}")
        elif mode == "clipboard":
            # v0.9.10 (Codex paste-reliability): text IS in the clipboard
            # — make the recovery action obvious. Many users assume
            # "copied to clipboard" means failure; spell out the Ctrl+V
            # gesture so they don't lose the transcript.
            if self.orb is None:
                self.bubble.show_state(
                    "done", "📋 in Zwischenablage — Strg+V drücken", auto_hide_ms=3500
                )
            else:
                self.orb.show_state("done")
            self.tray.set_state("done", "in Zwischenablage — Strg+V drücken")
            try:
                self.tray.showMessage(
                    "Sonar — bereit zum Einfügen",
                    "Text steht in der Zwischenablage. Strg+V drücken um manuell einzufügen.",
                    msecs=3500,
                )
                # v0.9.13 (Codex P1): superseding any pending update toast
                # so clicking this balloon doesn't trigger the installer.
                self._last_tray_balloon_was_update = False
            except Exception:
                pass
        else:
            # mode == 'none' → set_clipboard itself failed (rare; Linux
            # without xclip/wl-copy/Qt). Tell the user explicitly.
            if self.orb is None:
                self.bubble.show_state(
                    "error", "Zwischenablage fehlgeschlagen — Diagnose in Settings",
                    auto_hide_ms=4000,
                )
            else:
                self.orb.show_state("error")
            self.tray.set_state("error", "Zwischenablage fehlgeschlagen")
            try:
                self.tray.showMessage(
                    "Sonar — Fehler",
                    "Zwischenablage nicht erreichbar. Settings → Profil → Diagnose kopieren für Debug.",
                    msecs=5000,
                )
                # v0.9.13 (Codex P1): see note in the clipboard-success branch.
                self._last_tray_balloon_was_update = False
            except Exception:
                pass
        QTimer.singleShot(2500, lambda: (self.tray.set_state("idle", "idle"), self._safe_status("idle")))

    def _on_auto_mode_picked(self, style: str, label: str) -> None:
        """Auto-Mode chose a style different from the user's manual default.
        Show a small tray confirmation so they know what got applied."""
        labels = {
            "prompt": "Prompt",
            "email": "Email",
            "slack": "Slack",
            "formal": "Formal",
            "tidy": "Tidy",
        }
        nice = labels.get(style, style.title())
        self.tray.showMessage(
            "Sonar — Auto-Mode",
            f"{nice} (für {label})",
            msecs=1800,
        )

    def _on_transcribe_failed(self, message: str) -> None:
        lower = message.lower()
        # v0.3.22: trial expired surfaces as a paywall instead of an error
        # toast. The transcriber raises TrialExpiredError → the worker
        # forwards it via .failed.emit(str(e)); we recognise it by content.
        if "trial" in lower and ("expired" in lower or "ended" in lower) or "402" in lower:
            self.tray.set_state("idle", "idle")
            self._safe_status("idle")
            self.bubble.fade_out()
            self._show_paywall()
            return
        # Auth / credentials problems are user-fixable in Settings — surface a
        # dialog instead of just a tray flash that the user can't act on.
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

    def _show_paywall(self) -> None:
        from .ui.plan_badge import PaywallDialog
        from . import account as _acc

        url = _acc.upgrade_url(
            self.config.subunit_endpoint, self.config.subunit_api_key
        )
        dlg = PaywallDialog(url, parent=self.main_window)
        dlg.exec()
        if dlg.result_action() == "local":
            # Switch the desktop app back to free local mode in-place
            self.change_mode("local")
            self.tray.showMessage(
                "Sonar",
                "Switched to Local mode. Cloud disabled until you upgrade.",
                msecs=3500,
            )

    def _refresh_account_info(self) -> None:
        """Pull latest plan + trial state from the server in the background.
        Non-blocking, no error toast — if the server is down we just keep
        the local cache."""
        if not self.config.subunit_api_key:
            self.main_window.plan_badge.hide()
            return
        from . import account as _acc
        from .ui.plan_badge import update_badge_from_info

        class _W(QObject):
            done = pyqtSignal(object)

            def __init__(self, ep, key):
                super().__init__()
                self.ep, self.key = ep, key

            def run(self):
                info = _acc.info(self.ep, self.key)
                self.done.emit(info)

        thread = QThread(self)
        worker = _W(self.config.subunit_endpoint, self.config.subunit_api_key)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        def apply(info):
            update_badge_from_info(self.main_window.plan_badge, info)
            if info is not None:
                # Mirror server truth back into local config so the badge
                # is correct on the next launch even before we refetch.
                self.config.plan = info.plan
                if info.trial_started_at:
                    self.config.trial_started_at = info.trial_started_at
                self.config.save()

        worker.done.connect(apply)
        worker.done.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._account_refresh = (thread, worker)
        thread.start()

    def _show_error(self, message: str) -> None:
        _log.error("UI error surfaced: %s", message)
        self.tray.set_state("error", "error")
        self._safe_status("error", color="#ffc450")
        self.bubble.show_state("error", f"⚠ {message[:60]}", auto_hide_ms=5000)
        self.tray.showMessage("Sonar — error", message, msecs=4000)
        # v0.9.13 (Codex P1): supersede the update-toast click handler so
        # this error balloon doesn't trigger the installer dialog.
        self._last_tray_balloon_was_update = False
        QTimer.singleShot(3000, lambda: (self.tray.set_state("idle", "idle"), self._safe_status("idle")))

    def _record_history(
        self, text: str, mode: str, quality_mode: str = ""
    ) -> None:
        # v0.9.11: counters always tick (so the Settings "total transcriptions"
        # stat stays honest) but the per-snippet entry is skipped when the user
        # opted out of history. quality_mode is the cloud tier the server
        # actually ran ("instant" / "fast" / "quality") — empty for local
        # transcripts.
        self.config.total_transcriptions += 1
        if self._last_audio_seconds:
            self.config.total_audio_seconds += self._last_audio_seconds
        history_enabled = bool(getattr(self.config, "history_enabled", True))
        if history_enabled:
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "text": text,
                "mode": self.config.mode,
                "paste_mode": mode,
                "target": self.target.title if self.target else None,
                "quality_mode": quality_mode or "",
            }
            self.config.history.append(entry)
            self.config.history = self.config.history[-self.config.history_size :]
        self.config.save()
        try:
            self.main_window.refresh()
        except Exception:
            pass

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.config)
        if dlg.exec():
            old_hotkey = self.config.hotkey
            old_mode = self.config.recording_mode
            # v0.9.6 (TJ): remember orb-style + use_orb_overlay so we can
            # rebuild the live overlay without restart when the user
            # picks a different style.
            old_orb_style = getattr(self.config, "orb_overlay_style", "sphere")
            old_use_orb = bool(getattr(self.config, "use_orb_overlay", True))
            dlg.apply_to(self.config)
            # Settings can change credentials/endpoint/model — drop the cached
            # transcriber instances so the next call uses the new values.
            from .transcriber import clear_cache as _clear_transcriber_cache

            _clear_transcriber_cache()
            # Restart the listener if either the combo OR the recording mode
            # changed — `update()` swaps both atomically.
            if (
                self.config.hotkey != old_hotkey
                or self.config.recording_mode != old_mode
            ):
                self.hotkey.update(
                    self.config.hotkey, mode=self.config.recording_mode
                )
            # v0.9.6: live-apply orb-style change. Without this the user
            # picks "Sonar Ping" in Settings → saves → orb keeps showing
            # the old sphere until the next app launch.
            new_orb_style = getattr(self.config, "orb_overlay_style", "sphere")
            new_use_orb = bool(getattr(self.config, "use_orb_overlay", True))
            if new_use_orb != old_use_orb or new_orb_style != old_orb_style:
                self._recreate_orb_overlay()
            self.tray.set_mode(self.config.mode)
            try:
                self.main_window.refresh_mode()
            except Exception:
                pass
            self.tray.showMessage(
                "Sonar",
                f"Updated. Hotkey: {self.config.hotkey} · Mode: {self.config.mode}",
                msecs=2500,
            )

    def _recreate_orb_overlay(self) -> None:
        """Destroy + rebuild the overlay widget so a Settings change to
        ``orb_overlay_style`` or ``use_orb_overlay`` takes effect live
        without restarting the app."""
        try:
            if self.orb is not None:
                self.orb.hide()
                self.orb.deleteLater()
                self.orb = None
        except Exception:
            pass
        if self.config.use_orb_overlay:
            self.orb = OrbOverlay(self.config, on_change_mode=self.change_mode)
            self.orb.set_level_provider(lambda: self.recorder.level)
            # v0.9.17: respect auto-hide — start invisible, surface only on recording.
            if not getattr(self.config, "orb_overlay_auto_hide", False):
                self.orb.show()

    def _prompt_for_credentials(self, message: str) -> None:
        from PyQt6.QtWidgets import QMessageBox

        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Sonar")
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

    def _show_onboarding(self) -> None:
        """First-launch wizard. Modal — blocks until the user finishes."""
        from .ui.onboarding import OnboardingDialog

        dlg = OnboardingDialog(self.config, parent=self.main_window)

        def _apply(settings: dict) -> None:
            self.config.hotkey = settings.get("hotkey") or self.config.hotkey
            new_mode = settings.get("mode") or self.config.mode
            self.config.ui_language = settings.get("ui_language") or self.config.ui_language
            # v0.3.21: account + theme + trial
            new_theme = settings.get("ui_theme") or self.config.ui_theme
            self.config.ui_theme = new_theme
            self.config.account_email = settings.get("account_email", "") or self.config.account_email
            sub_key = settings.get("subunit_api_key", "")
            if sub_key:
                self.config.subunit_api_key = sub_key
            self.config.plan = settings.get("plan") or self.config.plan
            trial_ts = settings.get("trial_started_at") or 0
            if trial_ts:
                self.config.trial_started_at = trial_ts
            # v0.3.26: Auto-Mode opt-in from the new Onboarding page
            if "cleanup_auto_mode" in settings:
                self.config.cleanup_auto_mode = bool(settings["cleanup_auto_mode"])
                # Auto-Mode is meaningless without cleanup; if the user
                # opted in we flip cleanup_enabled too. They can still
                # toggle it off in Settings later.
                if settings["cleanup_auto_mode"]:
                    self.config.cleanup_enabled = True
            from . import i18n, theme as _theme
            i18n.set_language(self.config.ui_language)
            _theme.apply(QApplication.instance(), self.config.ui_theme)
            if new_mode != self.config.mode:
                # Route via change_mode so cache invalidation + tray update happens
                self.change_mode(new_mode)
            else:
                self.config.save()
            self.config.has_seen_onboarding = True
            self.config.save()
            # Re-bind hotkey listener if it changed. `update()` handles
            # stop+start atomically and uses the correct constructor kwargs;
            # recreating the GlobalHotkey here used the wrong kwarg names and
            # could leave the user with `has_seen_onboarding=True` and no
            # working hotkey listener.
            try:
                self.hotkey.update(
                    self.config.hotkey, mode=self.config.recording_mode
                )
            except Exception:
                _log.exception("Failed to update hotkey after onboarding")
            self.main_window.refresh()
            self.tray.showMessage(
                "Sonar",
                f"All set. Press {self.config.hotkey} to dictate.",
                msecs=4000,
            )

        dlg.finished_setup.connect(_apply)
        dlg.exec()
        # If the user closes via X without clicking Finish, still mark as seen
        if not self.config.has_seen_onboarding:
            self.config.has_seen_onboarding = True
            self.config.save()
        # TJ-feedback v0.3.18: after the wizard, open the main window
        # automatically. Closing into nothing felt like the app crashed.
        QTimer.singleShot(150, self.open_window)

    def open_history(self) -> None:
        def repaste(text: str) -> None:
            from .target_lock import set_clipboard
            set_clipboard(text)
            self.tray.showMessage(
                "Sonar", "History entry copied to clipboard", msecs=1500
            )

        dlg = HistoryDialog(self.config, on_repaste=repaste)
        dlg.exec()

    def open_meetings(self) -> None:
        """Open the Meetings browser (long-form recordings, task extraction)."""
        from .ui.meetings import MeetingsDialog
        dlg = MeetingsDialog(self.config)
        dlg.exec()

    def start_meeting(self) -> None:
        """Open the Meeting-Host modal — generate a QR + 6-digit code for
        the Subunit Meet PWA, watch participants check in live, and
        start/end the recording. (v0.9.0)
        """
        if not self.config.subunit_api_key:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self.main_window,
                "Subunit-Anmeldung erforderlich",
                "Du brauchst einen Subunit-Account um Meetings zu hosten.\n\n"
                "Öffne Settings → Account → 'Get my Subunit key' und log dich ein.",
            )
            return
        from .ui.meeting_host import MeetingHostDialog
        dlg = MeetingHostDialog(self.config, parent=self.main_window)
        dlg.exec()

    def change_mode(self, mode: str) -> None:
        self.config.mode = mode
        self.config.save()
        self.tray.set_mode(mode)
        self.tray.showMessage("Sonar", f"Mode: {mode}", msecs=1500)

    def _check_for_updates(self, interactive: bool = False) -> None:
        """Check GitHub for a newer release.

        Non-interactive (auto, at startup): silent if nothing new, tray
        toast + cached info if an update is available. The user can
        review/install later from Settings → Profile.

        Interactive (Profile "Update prüfen" button): always shows a
        result message so the user knows the check happened.
        """
        from PyQt6.QtWidgets import QMessageBox

        from . import updater

        info = updater.check()
        # Stash on the instance so the Profile card can offer "Install"
        # later without re-checking the GitHub endpoint.
        self._available_update = info if (info and info.available) else None

        if info is None or not info.available:
            if interactive:
                QMessageBox.information(
                    None, "Sonar — Update",
                    f"Du bist auf der neuesten Version (v{getattr(info, 'current', __version__)}).",
                )
            return

        _log.info("Update available %s → %s (interactive=%s)", info.current, info.latest, interactive)
        # 2026-05-16 (TJ-feedback v0.9.10): replace the auto-modal
        # QMessageBox with a non-blocking tray toast. Users hated being
        # interrupted mid-dictation 8s after launch. The full install
        # dialog is now reachable from the tray-toast click and from
        # Settings → Profile → "Update verfügbar".
        try:
            self.tray.showMessage(
                "Sonar — Update verfügbar",
                f"v{info.current} → {info.latest}. Klick zum Installieren oder über Settings.",
                msecs=6000,
            )
            # v0.9.13: arm the balloon-click handler. Auto-disarmed
            # after the toast duration so a click on any later toast
            # doesn't open the installer; also consumed on the first
            # actual click. Any OTHER showMessage call within those 7s
            # is also assumed to supersede the update toast — so we
            # disarm in _on_transcribe_done / _show_error explicitly.
            self._last_tray_balloon_was_update = True
            QTimer.singleShot(7000, self._clear_update_balloon_flag)
        except Exception:
            pass

        if interactive:
            # Open the full install dialog right away — the user asked.
            self._prompt_update_install(info)

    def _on_tray_balloon_clicked(self) -> None:
        """Tray balloon click handler. Opens the install prompt only
        when the LAST balloon shown was the update-available toast;
        otherwise raises the main window. v0.9.13 (Codex P1): without
        this guard, clicking a "paste ready" or "error" toast would
        open the installer dialog as long as `_available_update` was
        cached from an earlier check."""
        was_update = self._last_tray_balloon_was_update
        # One-shot — consume the flag on click so a follow-up generic
        # toast click doesn't reuse it.
        self._last_tray_balloon_was_update = False
        if was_update and self._available_update is not None:
            self._prompt_update_install(self._available_update)
        else:
            self.open_window()

    def _clear_update_balloon_flag(self) -> None:
        """Auto-disarm helper for the update-toast click handler.
        Scheduled by _check_for_updates 7s after the balloon fires."""
        self._last_tray_balloon_was_update = False

    def _prompt_update_install(self, info) -> None:
        """Full Yes/No install dialog. Spawned from tray-click or from
        the Profile card's "Update installieren" button."""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtWidgets import QMessageBox

        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Sonar — Update verfügbar")
        if info.installer_url:
            box.setText(
                f"Neue Version verfügbar:\n\n"
                f"  Aktuell: v{info.current}\n"
                f"  Neueste: {info.latest}\n\n"
                f"Jetzt herunterladen + installieren? Sonar wird neu gestartet."
            )
            box.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            box.setDefaultButton(QMessageBox.StandardButton.Yes)
            if box.exec() == QMessageBox.StandardButton.Yes:
                self._download_and_install(info)
        else:
            box.setText(
                f"Neue Version verfügbar:\n\n"
                f"  Aktuell: v{info.current}\n"
                f"  Neueste: {info.latest}\n\n"
                f"Release-Seite öffnen?"
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
        progress.setWindowTitle("Sonar — Updating")
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)

        class _Worker(QThread):
            progress_changed = pyqtSignal(int)
            finished_with_path = pyqtSignal(str)
            failed = pyqtSignal(str)

            def __init__(self, url: str, expected_hash: str = "", asset_name: str = "") -> None:
                super().__init__()
                self.url = url
                self.expected_hash = expected_hash
                self.asset_name = asset_name
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
                    target = updater.download_installer(
                        self.url,
                        progress_cb=cb,
                        expected_sha256=self.expected_hash or None,
                        asset_name=self.asset_name or None,
                    )
                    self.finished_with_path.emit(str(target))
                except Exception as e:
                    if not self._cancelled:
                        self.failed.emit(str(e))

        worker = _Worker(
            info.installer_url,
            info.installer_sha256 or "",
            info.installer_name or "",
        )
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
                    "Sonar — Update failed",
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
                "Sonar — Update failed",
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
        app.setApplicationName("Sonar")
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
        # group under "Sonar" instead of the generic Python interp.
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
                "Sonar",
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
                "Sonar — fatal error",
                f"{err}\n\nLog: {log_file}",
            )
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    sys.exit(main())
