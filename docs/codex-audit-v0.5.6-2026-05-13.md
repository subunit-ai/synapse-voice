# Codex (GPT-5.5) Audit of Sonar v0.5.6 — target_lock + updater

**Date:** 2026-05-13 16:55 CEST
**Reviewer:** GPT-5.5 via Codex CLI (xhigh reasoning)
**Subject:** target_lock.py + updater.py
**12 Findings, ranked by severity + likelihood of being the user's reported bug.**

## TOP 3 CRITICAL — explain why Dirk is stuck on v0.5.5

### #10. Updater filename from signed URL — file save fails (CRITICAL for auto-update)
`download_installer` uses `Path(resolved_url).name` where resolved_url is GitHub's signed redirect with query string. The "filename" becomes garbage like `2adfb435-1699-?sp=r&sv=...&filename=Sonar-Setup-0.5.6.exe&...` — contains `?`, `&`, `=` which are invalid Windows filename chars. **Download silently fails.**

**Fix:** Save using `UpdateInfo.installer_name` (original asset name) or parse `Content-Disposition`.

### #11. Updater not architecture-aware — wrong installer selected (CRITICAL for auto-update)
`_pick_installer_asset` returns the FIRST `.exe` containing `"setup"`. The v0.5.6 release has BOTH:
- `SynapseVoice-Setup-0.5.6-arm64.exe`
- `SynapseVoice-Setup-0.5.6.exe`

GitHub API returns ARM64 FIRST. So Win-x64 users get the ARM64 installer pushed by auto-update. Either fails to install, or installs ARM build that doesn't run.

**Fix:** Filter by host architecture using `IsWow64Process2` (detects emulated processes). x64 rejects `-arm64`, ARM64 prefers `-arm64`.

### #1. False "pasted" success → clipboard erased (CRITICAL paste path)
`main.py` saves clipboard, calls `paste_into`, then restores original clipboard 2.5s later when mode is `"pasted"`. But Windows paste verification is weak: `SendInput` only confirms keys queued, `WM_PASTE` only confirms delivery, `keybd_event` always returns True.

User experience: transcript briefly hits clipboard, paste-attempt reports success (even if Chrome didn't actually paste), 2.5s later clipboard is "restored" to whatever was there before — **transcript is gone**. User sees "nothing pasted, clipboard empty".

**Fix:** Remove `_win_keybd_paste` from success path. Verify paste via foreground/focus check before treating success as confirmed.

## HIGH SEVERITY

### #2. `_qt_set_clipboard` silent success — Qt clipboard race
`QClipboard.setText()` returns void; code immediately returns True. Silent failure modes: OLE lock, wrong thread, offscreen QPA, race before Qt publishes to OS.

**Fix:** After setText, `app.processEvents()` to flush, then verify via native `_win_get_clipboard()` readback, retry ~1s, then fall back to ctypes path.

### #3. Immediate paste after Qt set — race
`paste_into` sets clipboard then immediately focuses+pastes. Qt/OLE ownership might not have published yet.

**Fix:** Gate paste on verified native readback matching the text.

### #4. Win-ARM detection broken for emulated x64 builds
`_is_win_arm()` uses `platform.machine()` which returns AMD64 for x64-build-running-under-emulation on ARM machines. Code thinks it's Win-x64, reverts to WM_PASTE-first → breaks browser paste exactly like v0.5.4 did.

**Fix:** Use `IsWow64Process2` for native host architecture detection.

### #5. Attached-thread paste path ignores stale HWNDs / failed attach
No `IsWindow(hwnd)` validation, no `GetForegroundWindow() == hwnd` verification after SetForegroundWindow, ignores `AttachThreadInput` failures. Can paste nowhere but still return success.

### #6. `SetFocus(hwnd)` to top-level HWND breaks child-focus
Calling `SetFocus(hwnd)` on top-level HWND can destroy previous child focus. WM_PASTE then hits non-edit receiver.

**Fix:** Use `GetGUIThreadInfo(target_thread).hwndFocus` instead.

### #7. Missing ctypes signatures in focus/attach code (Medium-High)
Several Win32 calls lack `argtypes`/`restype`. On x64, HWND/DWORD can be truncated to c_int.

## MEDIUM

### #8. QGuiApplication-missing fallback re-introduces Win-ARM bug
### #9. Qt platform plugin discovery failure (mostly prevents startup)

## UPDATER-SPECIFIC

### #12. HEAD-redirect handling brittle
`_resolve_redirects` uses HEAD then GET no-redirects. GitHub may handle HEAD differently.

---

## CAUSE-CHAIN ANALYSIS for Dirk's bug

Most likely scenario (Win-x64 machine):
1. Dirk installs v0.5.5 manually
2. v0.5.5 has the v0.5.4 paste bugs (WM_PASTE-first kills Chrome-emulation, ctypes clipboard race)
3. Auto-updater runs, finds v0.5.6 release
4. BUG #11: selects ARM64 installer (first in API listing)
5. BUG #10: tries to save with garbage filename → silent fail
6. Dirk stuck on v0.5.5 forever
7. Manual report: "Auto-paste doesn't work, clipboard doesn't get populated"

Alternative scenario (Win-ARM machine):
1. Auto-update DOES work (right installer picked, garbage-filename hits same path but x64-vs-ARM correct)
2. v0.5.6 installed
3. BUG #4: emulation detection wrong → reverts to WM_PASTE-first
4. BUG #2: Qt clipboard silent failure
5. BUG #1: false-success → clipboard erased
6. Same symptoms

→ v0.5.7 must fix at minimum: #10, #11 (so auto-update works), #1 (so success is verified), #2 (so clipboard is verified), #4 (so arch detection is correct).
