# Synapse Voice

Hotkey-driven speech-to-text for the subunit ecosystem.
Press a global hotkey to start recording, press it again to stop, transcript is auto-pasted into the originating window.

## Status

Phase 2 polish — local + OpenRouter cloud, history viewer, click-to-record hotkey input,
audio-level waveform, smooth fade-in/out, model pre-warm. Subunit-server endpoint stubbed for Phase 3.

## Stack

- Python 3.12 + PyQt6 (system-tray app, floating bubble indicator near cursor)
- `sounddevice` audio capture (16 kHz mono float32)
- `pynput` global hotkey
- `faster-whisper` local transcription (lazy-loaded on first use)
- OpenRouter Whisper API for cloud mode
- Linux: `xdotool` + `xclip` for target-lock + paste
- Windows: `ctypes user32` (capture / focus / paste) — ready, untested

## Modes

| Mode        | Backend                              | DSGVO                |
| ----------- | ------------------------------------ | -------------------- |
| `local`     | faster-whisper on device             | ✅ 100% — no network |
| `openrouter`| `openai/whisper-large-v3` via OpenRouter | ⚠️ depends on routing |
| `subunit`   | `transcribe.subunit.ai` (Phase 3)    | ✅ EU-hosted         |

Toggle via tray menu or settings dialog.

## Setup

```bash
# System packages (Ubuntu/Debian)
sudo apt install xdotool xclip portaudio19-dev libxcb-cursor0

# Python venv + deps
cd ~/subunit/unitone/workspace/projects/synapse-voice
bash scripts/setup-venv.sh

# Run from a graphical session (needs DISPLAY)
bash scripts/run-dev.sh
```

Headless smoke test (no display required):

```bash
source .venv/bin/activate
QT_QPA_PLATFORM=offscreen python tests/smoke_test.py
```

## Configuration

Stored in `~/.config/synapse-voice/config.json` (auto-created on first run).
Edit via tray → Settings, or directly in the file.

Default hotkey: `<ctrl>+<shift>+<space>` (toggle).

## Target-Lock

When the hotkey fires, the active window + title is captured. After transcription,
that exact window is re-focused (even if you switched tabs in between) and the
text is pasted via `Ctrl+V`. If the window is gone or the focus call fails,
the text falls back to the clipboard with a notification.

## Building distributables (Phase 3)

- Linux AppImage: TODO via `linuxdeploy` + `pyinstaller`
- Windows `.exe`: TODO via `pyinstaller --onefile --noconsole`

## Project layout

```
synapse_voice/
├── main.py            entry point (tray + signals + worker thread)
├── config.py          ~/.config/synapse-voice/config.json
├── recorder.py        sounddevice capture
├── hotkey.py          pynput global hotkey
├── target_lock.py     active-window capture + autopaste
├── transcriber/
│   ├── base.py        dispatch
│   ├── local.py       faster-whisper
│   ├── openrouter.py  OpenRouter API
│   └── subunit.py     transcribe.subunit.ai (Phase 3 stub)
└── ui/
    ├── tray.py        QSystemTrayIcon + dynamic icon by state
    ├── bubble.py      frameless cursor-anchored indicator
    └── settings.py    settings dialog
```
