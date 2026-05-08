"""Target-Lock: capture active window at hotkey press, paste back later.

Linux: xdotool. Windows: ctypes user32 (Phase 1 = Linux-first; Windows TODO at packaging).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from dataclasses import dataclass


@dataclass
class WindowTarget:
    window_id: str
    title: str
    platform: str


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def capture_active_window() -> WindowTarget | None:
    """Snapshot the currently-focused window so we can paste back into it later."""
    if sys.platform.startswith("linux"):
        if not _have("xdotool"):
            return None
        try:
            wid = subprocess.check_output(["xdotool", "getactivewindow"], text=True).strip()
            title = subprocess.check_output(
                ["xdotool", "getwindowname", wid], text=True
            ).strip()
            return WindowTarget(window_id=wid, title=title, platform="linux")
        except subprocess.CalledProcessError:
            return None
    if sys.platform == "win32":
        try:
            import ctypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return WindowTarget(window_id=str(hwnd), title=buf.value, platform="win32")
        except Exception:
            return None
    return None


def focus_window(target: WindowTarget) -> bool:
    if target.platform == "linux" and _have("xdotool"):
        try:
            subprocess.run(
                ["xdotool", "windowactivate", "--sync", target.window_id],
                check=True,
                timeout=2,
            )
            time.sleep(0.05)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False
    if target.platform == "win32":
        try:
            return _win_focus(int(target.window_id))
        except Exception:
            return False
    return False


def _win_focus(hwnd: int) -> bool:
    """Bring an HWND to the foreground.

    Windows blocks SetForegroundWindow from foreign processes for security, so
    we use the AttachThreadInput trick: attach our thread's input queue to the
    foreground window's thread, set the focus, then detach. Also restore the
    window from minimized if needed.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # Restore if minimized
    SW_RESTORE = 9
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    # AllowSetForegroundWindow first — works in some cases
    ASFW_ANY = -1
    user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
    user32.AllowSetForegroundWindow(ASFW_ANY & 0xFFFFFFFF)

    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None)
    my_thread = kernel32.GetCurrentThreadId()
    attached = False
    if fg_thread and fg_thread != my_thread:
        attached = bool(user32.AttachThreadInput(my_thread, fg_thread, True))
    try:
        user32.BringWindowToTop(hwnd)
        result = bool(user32.SetForegroundWindow(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(my_thread, fg_thread, False)
    time.sleep(0.05)
    return result


def set_clipboard(text: str) -> bool:
    if sys.platform.startswith("linux"):
        if _have("xclip"):
            p = subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text.encode("utf-8"),
                check=False,
            )
            return p.returncode == 0
        if _have("wl-copy"):
            p = subprocess.run(["wl-copy"], input=text.encode("utf-8"), check=False)
            return p.returncode == 0
        return False
    if sys.platform == "win32":
        return _win_set_clipboard(text)
    return False


def _win_set_clipboard(text: str) -> bool:
    """Native-API clipboard set with proper x64 ctypes signatures.

    Without explicit argtypes/restype the ctypes default (c_int) truncates
    handles + pointers on 64-bit Windows, silently corrupting the clipboard
    handle and making subsequent reads return garbage or fail.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # Signatures
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL

    GMEM_MOVEABLE = 0x0002
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL

    CF_UNICODETEXT = 13
    data = text.encode("utf-16-le") + b"\x00\x00"

    # Open clipboard, retry up to 5x (some apps hold the clipboard briefly)
    for attempt in range(5):
        if user32.OpenClipboard(0):
            break
        time.sleep(0.04)
    else:
        return False

    handle = 0
    try:
        if not user32.EmptyClipboard():
            return False

        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return False

        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(handle)

        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        # On success the system owns the handle — must NOT free it.
        return True
    finally:
        user32.CloseClipboard()


def paste_keystroke() -> bool:
    """Send Ctrl+V to the now-focused window."""
    if sys.platform.startswith("linux") and _have("xdotool"):
        try:
            subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+v"], check=True, timeout=2)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False
    if sys.platform == "win32":
        return _win_paste_keystroke()
    return False


def _win_paste_keystroke() -> bool:
    """Send Ctrl+V via SendInput.

    keybd_event is deprecated and not reliable on modern Win11 (and gets
    intercepted by some games / per-app input filters). SendInput posts to
    the same input queue real keyboards use.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    VK_CONTROL = 0x11
    VK_V = 0x56

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_void_p),
        ]

    class _INPUTunion(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [
            ("type", wintypes.DWORD),
            ("u", _INPUTunion),
        ]

    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT

    def make(vk: int, up: bool) -> "INPUT":
        e = INPUT()
        e.type = INPUT_KEYBOARD
        e.ki.wVk = vk
        e.ki.wScan = 0
        e.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
        e.ki.time = 0
        e.ki.dwExtraInfo = None
        return e

    seq = (INPUT * 4)(
        make(VK_CONTROL, False),
        make(VK_V, False),
        make(VK_V, True),
        make(VK_CONTROL, True),
    )
    sent = user32.SendInput(4, seq, ctypes.sizeof(INPUT))
    return sent == 4


def paste_into(target: WindowTarget | None, text: str) -> tuple[bool, str]:
    """Returns (success, mode) where mode is 'pasted' | 'clipboard' | 'none'."""
    if not set_clipboard(text):
        return False, "none"
    if target is None:
        return True, "clipboard"
    if not focus_window(target):
        return True, "clipboard"
    if not paste_keystroke():
        return True, "clipboard"
    return True, "pasted"
