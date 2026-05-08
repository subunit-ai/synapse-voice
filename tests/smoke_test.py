"""Headless smoke test — runs without display via Qt offscreen platform.

Usage:
    cd synapse-voice
    source .venv/bin/activate
    QT_QPA_PLATFORM=offscreen python tests/smoke_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np

# Make package importable when running this file directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication


def main() -> int:
    app = QApplication(sys.argv)

    # 1. Config
    from synapse_voice.config import Config

    c = Config()
    assert c.hotkey == "<ctrl>+<shift>+<space>"
    assert c.mode == "local"
    print("✅ Config defaults")

    # 2. Recorder (no actual recording — just instantiation + idle state)
    from synapse_voice.recorder import Recorder

    r = Recorder()
    assert not r.is_recording
    # Save-wav with empty buffer
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        r.save_wav(np.zeros(160, dtype=np.float32), Path(f.name))
        assert Path(f.name).stat().st_size > 0
    print("✅ Recorder + save_wav")

    # 3. Transcriber dispatch — error paths (without making real calls)
    from synapse_voice.transcriber import TranscriberError, get_transcriber

    try:
        get_transcriber("openrouter", c)
        raise AssertionError("Expected TranscriberError without API key")
    except TranscriberError:
        pass
    print("✅ Transcriber dispatch + error path")

    # 4. Target-lock — capture (may return None in CI/no-display envs)
    from synapse_voice.target_lock import capture_active_window, set_clipboard

    t = capture_active_window()
    print(f"✅ capture_active_window → {t}")

    # set_clipboard returns False without xclip+display, that's fine
    set_clipboard("synapse-voice smoke test")
    print("✅ set_clipboard call (may be no-op without display)")

    # 5. UI instances — Bubble + Tray
    from synapse_voice.ui.bubble import Bubble
    from synapse_voice.ui.tray import Tray

    b = Bubble()
    b.show_state("recording", "smoke test", auto_hide_ms=100)
    print("✅ Bubble.show_state")

    tray = Tray(
        on_toggle_record=lambda: None,
        on_open_settings=lambda: None,
        on_open_history=lambda: None,
        on_change_mode=lambda m: None,
        on_quit=lambda: None,
        current_mode="local",
    )
    tray.set_state("recording", "tooltip-test")
    tray.set_mode("openrouter")
    print("✅ Tray instantiation + state changes")

    # 6. HotkeyCaptureButton + HistoryDialog
    from synapse_voice.ui.hotkey_capture import HotkeyCaptureButton
    from synapse_voice.ui.history import HistoryDialog

    btn = HotkeyCaptureButton(c.hotkey)
    assert btn.value() == c.hotkey
    btn.setValue("<ctrl>+<alt>+r")
    assert btn.value() == "<ctrl>+<alt>+r"
    print("✅ HotkeyCaptureButton")

    HistoryDialog(c, on_repaste=lambda t: None)  # instantiation only — never exec()'d
    print("✅ HistoryDialog instantiation")

    print("\nAll smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
