"""Cross-platform autostart toggle.

Windows: HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run registry value.
Linux:   ~/.config/autostart/synapse-voice.desktop (XDG Autostart spec).
macOS:   not implemented (LaunchAgents would be the place, but Mac is out of scope).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

APP_NAME = "Synapse Voice"
APP_KEY = "synapse-voice"


def _executable_path() -> str:
    """Path of the running executable (PyInstaller bundle or python script)."""
    if getattr(sys, "frozen", False):
        return sys.executable
    return f'"{sys.executable}" "{Path(__file__).resolve().parent.parent / "synapse_voice" / "main.py"}"'


def is_enabled() -> bool:
    if sys.platform == "win32":
        return _win_is_enabled()
    if sys.platform.startswith("linux"):
        return _linux_is_enabled()
    return False


def enable() -> bool:
    if sys.platform == "win32":
        return _win_enable()
    if sys.platform.startswith("linux"):
        return _linux_enable()
    return False


def disable() -> bool:
    if sys.platform == "win32":
        return _win_disable()
    if sys.platform.startswith("linux"):
        return _linux_disable()
    return False


# ---------- Windows (registry) ----------

def _win_open_run_key(write: bool):
    import winreg

    return winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_WRITE if write else winreg.KEY_READ,
    )


def _win_is_enabled() -> bool:
    try:
        import winreg

        with _win_open_run_key(write=False) as k:
            value, _ = winreg.QueryValueEx(k, APP_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except Exception:
        return False


def _win_enable() -> bool:
    try:
        import winreg

        path = _executable_path()
        with _win_open_run_key(write=True) as k:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, path)
        return True
    except Exception:
        return False


def _win_disable() -> bool:
    try:
        import winreg

        with _win_open_run_key(write=True) as k:
            winreg.DeleteValue(k, APP_NAME)
        return True
    except FileNotFoundError:
        return True
    except Exception:
        return False


# ---------- Linux (.desktop in ~/.config/autostart) ----------

def _linux_autostart_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / f"{APP_KEY}.desktop"


def _linux_desktop_content() -> str:
    return f"""[Desktop Entry]
Type=Application
Name={APP_NAME}
Comment=Hotkey-driven speech-to-text
Exec={_executable_path()}
Icon={APP_KEY}
Terminal=false
Categories=Utility;AudioVideo;
X-GNOME-Autostart-enabled=true
"""


def _linux_is_enabled() -> bool:
    return _linux_autostart_path().is_file()


def _linux_enable() -> bool:
    try:
        path = _linux_autostart_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_linux_desktop_content(), encoding="utf-8")
        return True
    except Exception:
        return False


def _linux_disable() -> bool:
    try:
        p = _linux_autostart_path()
        if p.exists():
            p.unlink()
        return True
    except Exception:
        return False
