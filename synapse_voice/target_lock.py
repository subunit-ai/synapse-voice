"""Target-Lock: capture active window at hotkey press, paste back later.

Linux: xdotool. Windows: ctypes user32 (Phase 1 = Linux-first; Windows TODO at packaging).
"""
from __future__ import annotations

import platform as _platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class WindowTarget:
    window_id: str
    title: str
    platform: str
    # v0.5.8: focused-child HWND captured at hotkey-press time, so we
    # can restore focus to the actual text input later instead of the
    # outer top-level HWND (which is what SetFocus on the parent gives
    # us — and which paints WM_PASTE into nowhere on browsers etc.).
    # None on non-Win platforms, or when GetGUIThreadInfo failed.
    focus_hwnd: Optional[int] = None


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _is_win_arm() -> bool:
    """True on Windows-on-ARM hosts (Snapdragon X / Surface Pro X).

    Native-ARM64 Sonar running against x64-emulated targets (Chrome,
    most other apps that don't ship ARM-native builds) has a different
    paste-keystroke story than native-x64: synthetic Ctrl+V's modifier
    state doesn't always propagate through the emulator's input-routing,
    so we have to prefer SendInput over WM_PASTE on this arch.

    v0.5.7 fix (Codex finding #4): the previous `platform.machine()`
    check reports AMD64 inside an x64 process running under emulation
    on a native-ARM64 host.  So if a user installs the x64 build of
    Sonar on a Snapdragon machine, we'd treat it as "real x64", apply
    the wrong paste strategy, and re-introduce the Chrome-on-ARM bug.
    Use IsWow64Process2 to ask the OS about the actual hardware.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        if hasattr(kernel32, "IsWow64Process2"):
            kernel32.IsWow64Process2.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.USHORT),
                ctypes.POINTER(wintypes.USHORT),
            ]
            kernel32.IsWow64Process2.restype = wintypes.BOOL
            proc = kernel32.GetCurrentProcess()
            proc_machine = wintypes.USHORT(0)
            native_machine = wintypes.USHORT(0)
            if kernel32.IsWow64Process2(
                proc,
                ctypes.byref(proc_machine),
                ctypes.byref(native_machine),
            ):
                # IMAGE_FILE_MACHINE_ARM64 = 0xAA64
                if native_machine.value == 0xAA64:
                    return True
    except Exception:
        pass
    # Fallback for older Windows (< 10 v1709) or detection failure.
    m = _platform.machine().lower()
    return m in ("arm64", "aarch64")


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
            import os as _os
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()

            # Guard: don't capture our own window as the paste target —
            # otherwise pressing the hotkey while Sonar itself has
            # focus would auto-paste back into our settings dialog.
            try:
                pid_buf = ctypes.c_ulong(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_buf))
                if pid_buf.value == _os.getpid():
                    return None
            except Exception:
                pass

            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)

            # v0.5.8: also capture the focused-child HWND.
            # When the user pressed our hotkey, their text cursor was
            # in some control inside `hwnd` — possibly a deeply nested
            # Edit / RichEdit / Chromium renderer child.  Save it now so
            # we can target WM_PASTE at the right HWND later instead of
            # the top-level (which v0.5.6's SetFocus(hwnd) was wrongly
            # routing to → "Erik: er springt raus aus dem Eingabefeld").
            focus_hwnd: Optional[int] = None
            try:
                target_tid = user32.GetWindowThreadProcessId(hwnd, None)
                if target_tid:
                    focus_hwnd = _win_focused_hwnd_for_thread(target_tid)
            except Exception:
                pass

            return WindowTarget(
                window_id=str(hwnd),
                title=buf.value,
                platform="win32",
                focus_hwnd=focus_hwnd,
            )
        except Exception:
            return None
    if sys.platform == "darwin":
        # macOS doesn't expose foreground-window HWNDs the same way; ask
        # the System Events scripting bridge for the frontmost app + its
        # active window title.  AppleScript via osascript works without
        # extra permissions for the *name* (Accessibility prompt only kicks
        # in when we later send keystrokes — handled in paste_keystroke()).
        try:
            script = (
                'tell application "System Events" to set frontApp to '
                'name of first application process whose frontmost is true'
            )
            name = subprocess.check_output(
                ["osascript", "-e", script], text=True, timeout=2
            ).strip()
            # Skip our own process as paste target.
            if name in ("Sonar", "synapse-voice", "Python"):
                return None
            return WindowTarget(window_id=name, title=name, platform="darwin")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
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
    if target.platform == "darwin":
        # `tell application "<name>" to activate` brings it to the front.
        # Quote the name so apps with spaces (e.g. "Visual Studio Code")
        # work; AppleScript single-quotes don't escape, double-quotes do.
        try:
            name = target.window_id.replace('"', '\\"')
            script = f'tell application "{name}" to activate'
            subprocess.run(
                ["osascript", "-e", script], check=True, timeout=2
            )
            time.sleep(0.1)  # let the front-most-change settle
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return False
    return False


def _win_focus(hwnd: int) -> bool:
    """Bring an HWND to the foreground.

    Windows blocks SetForegroundWindow from foreign processes for security.
    Stack of workarounds, in order:
      1. Restore window from minimized.
      2. AllowSetForegroundWindow(ASFW_ANY).
      3. The Alt-tap trick: simulate Alt down/up. This makes Windows treat
         our process as if the user just interacted, which lifts the
         foreground-lock for ~5 seconds.
      4. AttachThreadInput to the foreground window's thread.
      5. BringWindowToTop + SetForegroundWindow + SetActiveWindow + SetFocus
         (one of these usually sticks once 1-4 are in place).
      6. Detach.
    Then sleep 150ms so the focus actually propagates before paste fires.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    SW_RESTORE = 9
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    ASFW_ANY = -1
    try:
        user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
        user32.AllowSetForegroundWindow(ASFW_ANY & 0xFFFFFFFF)
    except Exception:
        pass

    # Alt-tap trick — fakes user interaction, lifts the foreground-lock.
    VK_MENU = 0x12  # Alt
    KEYEVENTF_KEYUP = 0x0002
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)

    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None)
    my_thread = kernel32.GetCurrentThreadId()
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    attached_fg = False
    attached_target = False
    if fg_thread and fg_thread != my_thread:
        attached_fg = bool(user32.AttachThreadInput(my_thread, fg_thread, True))
    if target_thread and target_thread != my_thread and target_thread != fg_thread:
        attached_target = bool(
            user32.AttachThreadInput(my_thread, target_thread, True)
        )
    try:
        user32.BringWindowToTop(hwnd)
        ok = bool(user32.SetForegroundWindow(hwnd))
        user32.SetActiveWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        if attached_fg:
            user32.AttachThreadInput(my_thread, fg_thread, False)
        if attached_target:
            user32.AttachThreadInput(my_thread, target_thread, False)

    # Give Windows time to repaint + transfer focus.
    time.sleep(0.15)
    if user32.GetForegroundWindow() == hwnd:
        return True

    # Retry once with a longer settle window — slow systems sometimes need
    # 200-300ms before SetForegroundWindow visibly applies.
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    if user32.GetForegroundWindow() == hwnd:
        return True
    return ok


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
        # Use Qt's clipboard instead of raw Win32 ctypes.  The previous
        # ctypes path worked on x64 but stops overwriting on Win-on-ARM
        # after the first call — likely because the emulated SetClipboardData
        # handle isn't released to the system the same way native-ARM expects.
        # QGuiApplication.clipboard() routes through Qt's well-tested platform
        # plugin that handles arch quirks correctly.
        return _qt_set_clipboard(text)
    if sys.platform == "darwin":
        # pbcopy ships in /usr/bin on every macOS install since 10.0.
        try:
            p = subprocess.run(
                ["pbcopy"], input=text.encode("utf-8"), check=False, timeout=2
            )
            return p.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False
    return False


def get_clipboard() -> Optional[str]:
    """Read the current clipboard contents. Returns None if unavailable
    (no clipboard tool, OS doesn't support, or read failed). Used by the
    paste flow to save+restore the user's original clipboard so we don't
    leak transcribed text to other apps after we're done."""
    if sys.platform.startswith("linux"):
        if _have("xclip"):
            try:
                p = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-o"],
                    capture_output=True, check=False, timeout=2,
                )
                if p.returncode == 0:
                    return p.stdout.decode("utf-8", errors="replace")
            except (subprocess.TimeoutExpired, OSError):
                pass
        if _have("wl-paste"):
            try:
                p = subprocess.run(
                    ["wl-paste", "--no-newline"],
                    capture_output=True, check=False, timeout=2,
                )
                if p.returncode == 0:
                    return p.stdout.decode("utf-8", errors="replace")
            except (subprocess.TimeoutExpired, OSError):
                pass
        return None
    if sys.platform == "win32":
        return _qt_get_clipboard()
    if sys.platform == "darwin":
        try:
            p = subprocess.run(
                ["pbpaste"], capture_output=True, check=False, timeout=2
            )
            if p.returncode == 0:
                return p.stdout.decode("utf-8", errors="replace")
        except (subprocess.TimeoutExpired, OSError):
            pass
        return None
    return None


def _qt_set_clipboard(text: str) -> bool:
    """Qt-routed clipboard set.  Falls back to the ctypes path only if
    Qt's QGuiApplication isn't initialised (e.g. test scaffolding).

    Why Qt instead of raw Win32:
      * Qt's platform plugin handles the WM_RENDERFORMAT / WM_DESTROYCLIPBOARD
        round-trip for us, which the bare ctypes path skips.  On Win-on-ARM
        x64-emulation the missing handshake was leaving stale clipboard
        contents after the first set (TJ-report, Erik on Surface Pro 2024:
        "Nach der ersten Transkription bleibt die Zwischenablage immer bei
        der ersten Aufnahme").
      * Qt sets BOTH CF_UNICODETEXT and CF_TEXT and announces the format
        list correctly so Chromium / Office / Notepad all pick it up.

    v0.5.7 (Codex findings #2/#3): QClipboard.setText() returns void —
    the previous code assumed "no exception = success", but Qt can fail
    silently when the OS clipboard is locked by another app, when we're
    on the wrong thread, or when the QPA backend is offscreen.  Now we
    verify the set via a native readback and fall back to the ctypes
    path on disagreement.
    """
    try:
        from PyQt6.QtCore import QCoreApplication
        from PyQt6.QtGui import QGuiApplication

        app = QGuiApplication.instance()
        if app is None:
            _log_paste("clipboard: QGuiApplication missing → ctypes fallback")
            return _win_set_clipboard(text)
        cb = app.clipboard()
        if cb is None:
            _log_paste("clipboard: QClipboard missing → ctypes fallback")
            return _win_set_clipboard(text)
        cb.setText(text)
        # Pump the Qt event loop so the OLE/Win32 clipboard hand-off
        # actually publishes before we paste.  Without this the
        # SetForegroundWindow + Ctrl+V that follows can fire before the
        # target app has the new content available.
        try:
            QCoreApplication.processEvents()
        except Exception:
            pass
        # Verify by reading back via the native clipboard API.  If Qt
        # silently dropped the set (OLE lock, race, wrong QPA) the
        # native read returns the previous contents or None.
        for _ in range(20):  # ~400ms total wait
            check = _win_get_clipboard()
            if check == text:
                return True
            time.sleep(0.02)
        _log_paste("clipboard: Qt setText didn't take, falling back to ctypes")
        return _win_set_clipboard(text)
    except Exception as e:
        _log_paste(f"clipboard: Qt path raised {type(e).__name__}: {e!s} → ctypes fallback")
        # Last-resort: try the ctypes path so we don't silently drop the
        # transcription if Qt's clipboard subsystem is unhappy.
        return _win_set_clipboard(text)


def _qt_get_clipboard() -> Optional[str]:
    """Qt-routed clipboard read.  Symmetric to _qt_set_clipboard."""
    try:
        from PyQt6.QtGui import QGuiApplication

        app = QGuiApplication.instance()
        if app is None:
            return _win_get_clipboard()
        cb = app.clipboard()
        if cb is None:
            return _win_get_clipboard()
        text = cb.text()
        return text if text else None
    except Exception:
        return _win_get_clipboard()


def _win_get_clipboard() -> Optional[str]:
    """Read CF_UNICODETEXT from the Win clipboard with proper x64 sigs."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL

    CF_UNICODETEXT = 13
    for _ in range(5):
        if user32.OpenClipboard(0):
            break
        time.sleep(0.04)
    else:
        return None
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


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
    if sys.platform == "darwin":
        # macOS uses Cmd+V, not Ctrl+V.  Easiest path: System Events
        # keystroke via osascript.  Requires Accessibility permission for
        # Sonar, which the user grants once in System Settings → Privacy &
        # Security → Accessibility.  Without it, this returns 0 success
        # and we fall back to clipboard-only mode.
        try:
            script = (
                'tell application "System Events" to keystroke "v" '
                'using command down'
            )
            p = subprocess.run(
                ["osascript", "-e", script],
                check=False, capture_output=True, timeout=3,
            )
            return p.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False
    return False


def _win_paste_keystroke() -> bool:
    """Send Ctrl+V via SendInput.

    keybd_event is deprecated and not reliable on modern Win11 (and gets
    intercepted by some games / per-app input filters). SendInput posts to
    the same input queue real keyboards use.

    The INPUT union MUST include MOUSEINPUT (and HARDWAREINPUT) even though
    we only use KEYBDINPUT — the OS's cbSize check is based on the full
    union's size (40 bytes on x64/arm64), and a "shrunken" union sized only
    for KEYBDINPUT (32 bytes) makes SendInput silently return 0 on
    Windows-on-ARM x64 emulation.  On native x64 this happened to work by
    chance because the kernel's parser only reads the KEYBDINPUT bytes
    when the type is INPUT_KEYBOARD, but that's not guaranteed.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    VK_CONTROL = 0x11
    VK_V = 0x56

    # ULONG_PTR is pointer-sized — c_void_p picks the right width on every
    # arch (8 bytes on x64/arm64, 4 bytes on x86).
    ULONG_PTR = ctypes.c_void_p

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUTunion(ctypes.Union):
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
            ("hi", HARDWAREINPUT),
        ]

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
    cb = ctypes.sizeof(INPUT)
    sent = user32.SendInput(4, seq, cb)
    if sent != 4:
        # GetLastError straight from kernel32 — ctypes.get_last_error() only
        # works if the DLL was opened with use_last_error=True (we use
        # plain windll, so it'd just return 0).  Common error codes we'd
        # see here: 5 = ERROR_ACCESS_DENIED (UIPI: low-IL → high-IL),
        # 87 = ERROR_INVALID_PARAMETER (cbSize wrong / struct corrupt).
        try:
            err = ctypes.windll.kernel32.GetLastError()
        except Exception:
            err = -1
        _log_paste(f"SendInput sent={sent}/4 cbSize={cb} lastError={err}")
    return sent == 4


def paste_into(target: WindowTarget | None, text: str) -> tuple[bool, str]:
    """Returns (success, mode) where mode is 'pasted' | 'clipboard' | 'none'."""
    if not set_clipboard(text):
        _log_paste("set_clipboard failed → none")
        return False, "none"
    if target is None:
        _log_paste("no target → clipboard only")
        return True, "clipboard"

    # Win path: attach + focus + paste in ONE AttachThreadInput context.
    # The previous version detached before pasting, which silently broke
    # SendInput (returns sent=0) and made GetFocus return NULL — so
    # WM_PASTE always fell back to the wrong (top-level) HWND.
    if target.platform == "win32":
        return _win_paste_attached(
            int(target.window_id),
            target.title or "",
            captured_focus_hwnd=target.focus_hwnd,
        )

    # Linux: xdotool already activated the window, single shot keystroke.
    if not focus_window(target):
        return True, "clipboard"
    if not paste_keystroke():
        return True, "clipboard"
    return True, "pasted"


def _win_paste_attached(
    hwnd: int,
    title: str,
    captured_focus_hwnd: Optional[int] = None,
) -> tuple[bool, str]:
    """Win-safe paste: attach input queues, focus, paste, detach — atomic.

    Why it has to be one block: Windows' input-queue isolation drops
    SendInput from a non-attached caller (sent=0) and GetFocus only
    returns the focused HWND for the calling thread. Keeping the
    AttachThreadInput open across the paste means SendInput injects
    into the target's queue and GetFocus returns the real focused
    descendant (e.g. Win11 Notepad's RichEditD2DPT, which is what
    actually accepts WM_PASTE — the top-level Notepad HWND ignores it).

    v0.5.8 (Codex finding #6 + Erik-report): `SetFocus(hwnd)` on the
    top-level HWND destroys the child-focus that was active when the
    user pressed our hotkey — visible effect: "er springt raus aus
    dem Eingabefeld".  Instead, we now use `captured_focus_hwnd`
    (snapshot taken at hotkey-press via GetGUIThreadInfo) as the
    focus target, and never call SetFocus on the parent.  If
    captured_focus_hwnd is stale (IsWindow() false), fall back to
    SetForegroundWindow alone and let Windows restore the natural
    last-focused descendant.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    SW_RESTORE = 9
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    try:
        user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
        user32.AllowSetForegroundWindow(0xFFFFFFFF)  # ASFW_ANY
    except Exception:
        pass

    # v0.5.9: lift the foreground-lock via SystemParametersInfoW instead
    # of the old Alt-tap trick.  Alt-tap synthesises a real Alt-down/up
    # which doubles as the menu-activation key in many apps: Chrome
    # focuses the URL bar / hamburger menu, Office Apps focus the
    # ribbon, generic apps enter "menu-navigation mode" where the next
    # keystroke (our Ctrl+V) is consumed by the menu instead of the
    # text input.  TJ-report from Erik on v0.5.8:
    #   "springt aufs Menu / anderes Feld blau umrandet" (Office, Word)
    #   "Chrome paste geht nicht mehr"
    # SystemParametersInfo(SPI_SETFOREGROUNDLOCKTIMEOUT, 0) sets the OS
    # foreground-lock timeout to zero, so SetForegroundWindow goes
    # through immediately without any synthetic input.  We restore the
    # original timeout afterwards in the finally block so we don't
    # leave the user's system permanently in "no-lock" mode.
    SPI_GETFOREGROUNDLOCKTIMEOUT = 0x2000
    SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001
    SPIF_SENDCHANGE = 0x0002
    _orig_lock_timeout = ctypes.c_uint(0)
    _lock_timeout_lifted = False
    try:
        user32.SystemParametersInfoW.argtypes = [
            wintypes.UINT,
            wintypes.UINT,
            ctypes.c_void_p,
            wintypes.UINT,
        ]
        user32.SystemParametersInfoW.restype = wintypes.BOOL
        if user32.SystemParametersInfoW(
            SPI_GETFOREGROUNDLOCKTIMEOUT,
            0,
            ctypes.byref(_orig_lock_timeout),
            0,
        ):
            # uiParam carries the timeout value when SPIF is SET — must
            # be cast to c_void_p (Win API takes it as ULONG_PTR).
            user32.SystemParametersInfoW(
                SPI_SETFOREGROUNDLOCKTIMEOUT,
                0,
                ctypes.c_void_p(0),
                SPIF_SENDCHANGE,
            )
            _lock_timeout_lifted = True
    except Exception:
        pass

    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    my_thread = kernel32.GetCurrentThreadId()
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)

    attached_fg = False
    attached_target = False
    if fg_thread and fg_thread != my_thread:
        attached_fg = bool(user32.AttachThreadInput(my_thread, fg_thread, True))
    if target_thread and target_thread != my_thread and target_thread != fg_thread:
        attached_target = bool(
            user32.AttachThreadInput(my_thread, target_thread, True)
        )

    pasted = False
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)

        # v0.5.8: ONLY SetFocus to the captured child, never to the
        # top-level.  Top-level SetFocus destroyed the in-input cursor
        # for Erik on Surface Pro.  If we never captured a child,
        # don't call SetFocus at all — SetForegroundWindow alone is
        # what users expect (restores last child focus naturally).
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        focus_target: Optional[int] = None
        if captured_focus_hwnd and user32.IsWindow(captured_focus_hwnd):
            try:
                user32.SetFocus.argtypes = [wintypes.HWND]
                user32.SetFocus.restype = wintypes.HWND
                user32.SetFocus(captured_focus_hwnd)
                focus_target = captured_focus_hwnd
            except Exception:
                pass
        time.sleep(0.2)

        # Under attachment, GetFocus returns the actual focused descendant.
        user32.GetFocus.restype = wintypes.HWND
        focused = focus_target or user32.GetFocus()
        focused_class = _win_class_name(focused) if focused else "<none>"
        capture_note = (
            f" captured={captured_focus_hwnd}"
            if captured_focus_hwnd else " (no capture)"
        )
        _log_paste(
            f"attached: target={hwnd} ({title[:40]!r}) "
            f"focused={focused} class={focused_class}{capture_note}"
        )

        # Choose strategy order based on (a) the focused window class and
        # (b) the host architecture.  Chromium-based browsers (Chrome /
        # Edge / Brave / Opera) and Firefox have a single top-level HWND
        # that receives keystrokes and forwards them to a separate
        # renderer process.
        #
        # Win-x64: WM_PASTE on the outer HWND works — Chromium's
        # RenderWidgetHostViewWin::WindowProc handles it by calling
        # delegate->Paste() directly.  Prefer it (no synthetic modifier
        # state to propagate).
        #
        # Win-on-ARM: Chromium ships as x64-emulated.  WM_PASTE *delivery*
        # succeeds (SendMessageTimeout returns non-zero) but the renderer
        # process doesn't always pick up the paste command across the
        # emulation boundary — TJ-report from Erik's Surface Pro X 2024:
        # "Text landet in der Zwischenablage, wird aber nicht automatisch
        # ins aktive Fenster eingefügt".  On this arch, fall back to
        # SendInput FIRST since it does work for Chrome-on-emulation if
        # the cbSize is correct (which v0.5.3 already fixed).
        is_browser = _is_browser_class(focused_class)
        is_win_arm = _is_win_arm()

        if is_browser and not is_win_arm:
            # Win-x64 + browser: WM_PASTE → SendInput fallback.
            if focused and _win_send_paste(focused):
                _log_paste(f"strategy WM_PASTE focused={focused} (browser x64) → True")
                pasted = True
            elif _win_paste_keystroke():
                _log_paste("strategy SendInput (browser x64 fallback) → True")
                pasted = True
        else:
            # Win-ARM (any target) OR non-browser: SendInput first.
            # Belt-and-braces: even if SendInput reports success, on
            # Win-ARM-browsers also follow up with WM_PASTE (cheap, no
            # double-paste risk because Chromium's renderer dedupes
            # identical paste commands within ~50ms).
            if _win_paste_keystroke():
                _log_paste(
                    f"strategy SendInput (arm={is_win_arm}, browser={is_browser}) → True"
                )
                pasted = True
                if is_win_arm and is_browser and focused:
                    # Extra safety net — costs nothing, sometimes the only
                    # path that actually triggers the paste on emulated
                    # Chromium.
                    _win_send_paste(focused)
            elif focused and _win_send_paste(focused):
                _log_paste(f"strategy WM_PASTE focused={focused} → True")
                pasted = True

        # Strategy 3: Walk descendants for any Edit-like control and try
        # WM_PASTE on each. Catches apps where the focused HWND isn't the
        # actual paste-receiver (e.g. some custom containers).
        if not pasted:
            for child, cls in _enum_edit_children(hwnd):
                if _win_send_paste(child):
                    _log_paste(f"strategy WM_PASTE child={child} class={cls} → True")
                    pasted = True
                    break

        # Strategy 4: legacy keybd_event — last resort. Some old native
        # apps respect it even when SendInput is silently filtered.
        # v0.5.7 (Codex finding #1): keybd_event ALWAYS returns True
        # unless we throw, so we can't trust it as a success signal.
        # Still fire it as a side-effect (might genuinely paste in some
        # old native apps) but do NOT flip `pasted=True`.  This prevents
        # the "paste reported success but didn't happen → clipboard
        # restore wipes the transcript" failure mode users were hitting.
        if not pasted:
            _win_keybd_paste()
            _log_paste("strategy keybd_event fired (unverified, not counted as paste)")
    finally:
        # v0.5.9: restore the original foreground-lock timeout. We must
        # always do this — leaving it at zero would mean any background
        # process could steal user focus until the next reboot.
        if _lock_timeout_lifted:
            try:
                user32.SystemParametersInfoW(
                    SPI_SETFOREGROUNDLOCKTIMEOUT,
                    0,
                    ctypes.c_void_p(int(_orig_lock_timeout.value)),
                    SPIF_SENDCHANGE,
                )
            except Exception:
                pass
        if attached_fg:
            user32.AttachThreadInput(my_thread, fg_thread, False)
        if attached_target:
            user32.AttachThreadInput(my_thread, target_thread, False)

    if pasted:
        return True, "pasted"
    _log_paste("all strategies failed → clipboard only")
    return True, "clipboard"


def _win_focused_hwnd_for_thread(thread_id: int) -> Optional[int]:
    """Return the HWND that owns the keyboard focus for `thread_id`.

    Uses `GetGUIThreadInfo(thread_id, &info)` so we get the focus state
    of an *external* GUI thread — which is what we need at hotkey-press
    time, before our own thread takes focus.  Returns None on failure
    or if the thread has no focused window.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _RECT(ctypes.Structure):
            _fields_ = [
                ("left", wintypes.LONG),
                ("top", wintypes.LONG),
                ("right", wintypes.LONG),
                ("bottom", wintypes.LONG),
            ]

        class _GUITHREADINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND),
                ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND),
                ("hwndCaret", wintypes.HWND),
                ("rcCaret", _RECT),
            ]

        user32 = ctypes.windll.user32
        user32.GetGUIThreadInfo.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(_GUITHREADINFO),
        ]
        user32.GetGUIThreadInfo.restype = wintypes.BOOL
        info = _GUITHREADINFO()
        info.cbSize = ctypes.sizeof(_GUITHREADINFO)
        if user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
            if info.hwndFocus:
                return int(info.hwndFocus)
    except Exception:
        return None
    return None


def _is_browser_class(cls: str) -> bool:
    """True for top-level HWND classes that belong to browsers whose paste
    target lives in a separate renderer process (so synthetic Ctrl+V via
    SendInput is unreliable, but WM_PASTE on the outer HWND works).

    Covered:
      * `Chrome_WidgetWin_1` / `Chrome_WidgetWin_0` — Chrome, Edge, Brave,
        Opera, Vivaldi, all other Chromium forks.
      * `MozillaWindowClass` — Firefox + Thunderbird (Gecko-based).
    """
    if not cls:
        return False
    if cls.startswith("Chrome_WidgetWin"):
        return True
    if cls == "MozillaWindowClass":
        return True
    return False


def _win_class_name(hwnd: int) -> str:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _enum_edit_children(hwnd: int) -> list[tuple[int, str]]:
    """Walk every descendant HWND, return (handle, class) for any control
    whose class looks like a text input. Covers Edit / RichEdit family +
    Win11 Notepad's RichEditD2DPT + Scintilla (used by VSCode etc.)."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    # Explicit signatures — without these, ctypes' default c_int truncates
    # HWND/LPARAM on x64 Windows and the call randomly returns garbage.
    user32.EnumChildWindows.argtypes = [
        wintypes.HWND, ctypes.c_void_p, wintypes.LPARAM
    ]
    user32.EnumChildWindows.restype = wintypes.BOOL

    EDIT_HINTS = ("Edit", "RichEdit", "RICHEDIT", "Scintilla", "RichEditD2DPT")
    found: list[tuple[int, str]] = []

    EnumChildProc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )

    def _cb(child: int, _lparam: int) -> int:
        cls = _win_class_name(child)
        if any(hint in cls for hint in EDIT_HINTS):
            found.append((child, cls))
        return 1  # BOOL TRUE — keep enumerating (Python True works too via
        # implicit cast but explicit int avoids a subtle ctypes warning).

    # Bind the callback object to a local so it isn't GC'd before the API
    # finishes iterating — Python could otherwise free the WINFUNCTYPE
    # wrapper mid-call and crash inside user32.
    callback = EnumChildProc(_cb)
    user32.EnumChildWindows(hwnd, callback, 0)
    return found


def _win_send_paste(hwnd: int) -> bool:
    """SendMessageTimeout(WM_PASTE) — blocks until the receiver processes
    the message (or 300ms timeout). Unlike PostMessage, gives a real
    "did the window accept this" indicator instead of "did we queue it"."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.SendMessageTimeoutW.restype = ctypes.c_void_p

    WM_PASTE = 0x0302
    SMTO_ABORTIFHUNG = 0x0002
    result = wintypes.DWORD(0)
    rc = user32.SendMessageTimeoutW(
        hwnd, WM_PASTE, 0, 0, SMTO_ABORTIFHUNG, 300, ctypes.byref(result)
    )
    return rc != 0  # nonzero = receiver processed the message


def _log_paste(msg: str) -> None:
    """Best-effort logger — silent if the logger isn't initialised yet."""
    try:
        from .logger import get as _get_logger

        _get_logger(__name__).info("paste: %s", msg)
    except Exception:
        pass


def _win_keybd_paste() -> bool:
    """Legacy keybd_event Ctrl+V — tried after SendInput as a fallback."""
    try:
        import ctypes

        VK_CONTROL = 0x11
        VK_V = 0x56
        KEYEVENTF_KEYUP = 0x0002
        user32 = ctypes.windll.user32
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(VK_V, 0, 0, 0)
        user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        return True
    except Exception:
        return False


def _win_post_paste(hwnd: int) -> bool:
    """Send WM_PASTE directly to the target HWND.

    `WM_PASTE = 0x0302` — handled natively by EDIT, RICHEDIT and any window
    that subclasses them. Bypasses keyboard simulation entirely so it isn't
    susceptible to focus-stealing or input-filter policies.
    """
    try:
        import ctypes
        from ctypes import wintypes

        WM_PASTE = 0x0302
        user32 = ctypes.windll.user32
        # Try sending to the focused child first — most edit controls live
        # inside a parent window's HWND, but the focus might have landed on
        # a specific child after our SetForegroundWindow call.
        user32.GetFocus.restype = wintypes.HWND
        focus = user32.GetFocus()
        if focus:
            user32.PostMessageW(focus, WM_PASTE, 0, 0)
        user32.PostMessageW(hwnd, WM_PASTE, 0, 0)
        return True
    except Exception:
        return False
