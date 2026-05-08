# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Synapse Voice — produces a single self-contained executable.
#
# Build (Linux):
#   cd ~/subunit/unitone/workspace/projects/synapse-voice
#   source .venv/bin/activate
#   pip install pyinstaller
#   pyinstaller --clean dist-build/synapse-voice.spec
#   → dist/synapse-voice/synapse-voice
#
# Build (Windows): same command, .exe is produced.

from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / "dist-build" / "entrypoint.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "synapse_voice",
        "synapse_voice.transcriber",
        "synapse_voice.transcriber.local",
        "synapse_voice.transcriber.openrouter",
        "synapse_voice.transcriber.subunit",
        "synapse_voice.ui",
        "synapse_voice.ui.tray",
        "synapse_voice.ui.bubble",
        "synapse_voice.ui.settings",
        "synapse_voice.ui.history",
        "synapse_voice.ui.hotkey_capture",
        # faster-whisper deps that PyInstaller's static analysis sometimes misses
        "faster_whisper",
        "ctranslate2",
        "tokenizers",
        "huggingface_hub",
        "av",
        "onnxruntime",
        # pynput backends
        "pynput.keyboard._xorg",
        "pynput.keyboard._win32",
        "pynput.mouse._xorg",
        "pynput.mouse._win32",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "PyQt5",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="synapse-voice",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI app — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="synapse-voice",
)
