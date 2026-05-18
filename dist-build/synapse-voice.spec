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

import importlib.util
import sys
from pathlib import Path
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)


def _safe_metadata(pkg: str) -> list:
    """Return copy_metadata() output if the package is installed, else []."""
    try:
        return copy_metadata(pkg)
    except Exception:
        return []

block_cipher = None
ROOT = Path(SPECPATH).parent


def _read_version() -> str:
    """Pull __version__ from the package __init__ at spec-eval time.
    Avoids drifting between `synapse_voice/__init__.py` and the
    Info.plist version PyInstaller writes into Sonar.app on macOS."""
    init_py = ROOT / "synapse_voice" / "__init__.py"
    for line in init_py.read_text().splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "0.0.0"


_VERSION = _read_version()


def _has(pkg: str) -> bool:
    """True if the package can be imported in the current build env.  We
    use this to keep the spec architecture-aware: on x64 we ship
    ctranslate2 + faster-whisper, on Win-ARM64 we ship onnx-asr instead."""
    return importlib.util.find_spec(pkg) is not None


extra_datas = []
extra_binaries = []
extra_hidden = []

# ── x64 backend: faster-whisper + ctranslate2 ───────────────────────────
# ctranslate2 has no Win-ARM64 wheel as of 2026Q2, so faster-whisper is
# absent on that runner.  collect_data_files would explode → guard.
if _has("faster_whisper"):
    extra_datas += collect_data_files("faster_whisper")
    extra_hidden += collect_submodules("faster_whisper")
if _has("tokenizers"):
    extra_datas += collect_data_files("tokenizers")
if _has("ctranslate2"):
    extra_binaries += collect_dynamic_libs("ctranslate2")

# ── ARM64 backend: onnx-asr + onnxruntime ───────────────────────────────
# onnx-asr ships its preprocessor ONNX files + Python source split across
# adapters.py / loader.py / asr.py / models/.  PyInstaller's static
# analysis only follows the entrypoint's import graph and easily misses
# the dynamic loader inside onnx_asr.load_model — collect_submodules
# enumerates EVERY .py module in the package so the bundle has them all.
if _has("onnx_asr"):
    extra_datas += collect_data_files("onnx_asr")
    extra_hidden += collect_submodules("onnx_asr")
    # onnx-asr does importlib.metadata.version("onnx-asr") in its __init__,
    # which raises PackageNotFoundError unless the *.dist-info dir ships too.
    # collect_data_files only grabs runtime files, NOT install metadata.
    extra_datas += _safe_metadata("onnx-asr")
if _has("huggingface_hub"):
    extra_datas += collect_data_files("huggingface_hub")
    extra_hidden += collect_submodules("huggingface_hub")
    extra_datas += _safe_metadata("huggingface_hub")

# onnxruntime ships native shared libs on every platform we target.
if _has("onnxruntime"):
    extra_binaries += collect_dynamic_libs("onnxruntime")
    extra_hidden += collect_submodules("onnxruntime")
    extra_datas += _safe_metadata("onnxruntime")

# Other packages that may read their own version via importlib.metadata
# at import time — bundle the dist-info so PackageNotFoundError doesn't fire.
for _meta_pkg in ("tokenizers", "numpy", "soundfile", "librosa"):
    extra_datas += _safe_metadata(_meta_pkg)

# Brand assets — the icons/ folder must ship with the bundle so the
# BrandLogo widget + tray icon can find subunit-logo.png at runtime.
extra_datas.append((str(ROOT / "icons" / "subunit-logo.png"), "icons"))
# Sound effects — synapse_voice/sounds/{start,done}.wav. PyInstaller's
# static analysis doesn't pick up arbitrary data dirs inside packages,
# so list them explicitly. Destination matches sounds._candidates() so
# the runtime resolver finds them via sys._MEIPASS.
extra_datas.append(
    (str(ROOT / "synapse_voice" / "sounds" / "start.wav"), "synapse_voice/sounds")
)
extra_datas.append(
    (str(ROOT / "synapse_voice" / "sounds" / "done.wav"), "synapse_voice/sounds")
)

a = Analysis(
    [str(ROOT / "dist-build" / "entrypoint.py")],
    pathex=[str(ROOT)],
    binaries=extra_binaries,
    datas=extra_datas,
    hiddenimports=[
        "synapse_voice",
        "synapse_voice.account",
        "synapse_voice.auto_mode",
        "synapse_voice.sounds",
        "synapse_voice.i18n",
        "synapse_voice.theme",
        "synapse_voice.languages",
        "synapse_voice.autostart",
        "synapse_voice.cleanup_client",
        "synapse_voice.hardware",
        "synapse_voice.logger",
        "synapse_voice.updater",
        "synapse_voice.transcriber",
        "synapse_voice.transcriber.local",
        "synapse_voice.transcriber.onnx_local",
        "synapse_voice.transcriber.cloud",
        "synapse_voice.transcriber.subunit",
        "synapse_voice.ui",
        "synapse_voice.ui.plan_badge",
        "synapse_voice.ui.tray",
        "synapse_voice.ui.bubble",
        "synapse_voice.ui.orb_overlay",
        "synapse_voice.ui.lang_picker",
        "synapse_voice.ui.mic_meter",
        "synapse_voice.ui.onboarding",
        "synapse_voice.languages",
        "synapse_voice.ui.settings",
        "synapse_voice.ui.history",
        "synapse_voice.ui.hotkey_capture",
        "synapse_voice.ui.main_window",
        "synapse_voice.ui.widgets",
        # v0.10.0 Hub
        "synapse_voice.ui.hub",
        "synapse_voice.ui.hub_header",
        "synapse_voice.ui.hub_sidebar",
        "synapse_voice.ui.sections",
        "synapse_voice.ui.sections.home",
        "synapse_voice.ui.sections.placeholder",
        "synapse_voice.ui.sections.settings",
        "synapse_voice.ui.sections.embed",
        # Local backend deps — only one of these two paths is installed
        # per architecture.  PyInstaller emits a warning for the absent
        # set; that's fine, the actual install handles arch-conditioning.
        "faster_whisper",       # x64 backend
        "ctranslate2",          # x64 backend
        "av",                   # x64 backend
        "onnx_asr",             # ARM64 backend (Win)
        # Shared by both
        "tokenizers",
        "huggingface_hub",
        "onnxruntime",
        # Auto-discovered submodules from collect_submodules above —
        # appended at the end so the explicit list above stays readable.
    ] + extra_hidden + [
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

# Platform-specific icon: .ico on Windows, .icns on macOS, none on Linux
# (PyInstaller refuses to embed cross-format icons — passing the .ico on
# macOS would error rather than fall back gracefully).
if sys.platform == "win32":
    _icon = str(ROOT / "icons" / "subunit-logo.ico")
elif sys.platform == "darwin":
    _icns_path = ROOT / "icons" / "subunit-logo.icns"
    _icon = str(_icns_path) if _icns_path.exists() else None
else:
    _icon = None

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
    # version-info.txt is Windows-only (PE VERSIONINFO resource); PyInstaller
    # ignores it on other platforms but errors if the file is missing — keep
    # the path set unconditionally.
    version=str(ROOT / "dist-build" / "version-info.txt"),
    icon=_icon,
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

# macOS: wrap the COLLECT output in a proper .app bundle so Finder treats
# Sonar as a single launchable application instead of a directory of files.
# Info.plist keys serve double duty: NSMicrophoneUsageDescription is
# REQUIRED by macOS TCC — first time the app accesses the mic, this string
# shows in the permission dialog (no string ⇒ macOS denies the call
# silently and the app can't record).  NSAppleEventsUsageDescription is
# needed for the osascript-driven autopaste path in target_lock.py.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Sonar.app",
        icon=_icon,
        bundle_identifier="ai.subunit.sonar",
        version=_VERSION,
        info_plist={
            "CFBundleShortVersionString": _VERSION,
            "CFBundleVersion": _VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "NSMicrophoneUsageDescription":
                "Sonar uses your microphone to record audio for transcription.",
            "NSAppleEventsUsageDescription":
                "Sonar uses Apple Events to paste transcribed text into the "
                "currently focused application.",
            # We're a tray-only app — LSUIElement hides the Dock icon so
            # Sonar lives in the menubar without cluttering the Dock.
            "LSUIElement": True,
        },
    )
