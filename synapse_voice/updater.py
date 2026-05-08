"""Auto-update checker — polls GitHub Releases for a newer version.

Network call is best-effort and timeouts quickly so app startup isn't
delayed if GitHub is slow. v0.3.4: result drives an in-app download +
installer launch instead of just opening the release page in a browser.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import requests

from . import __version__
from .logger import get as _get_logger

_log = _get_logger(__name__)

GITHUB_LATEST = "https://api.github.com/repos/subunit-ai/synapse-voice/releases/latest"


@dataclass
class UpdateInfo:
    current: str
    latest: str
    release_url: str
    body: str
    available: bool
    # v0.3.4: direct download URL for the platform-appropriate installer.
    # None if the release didn't ship one for our platform — caller falls
    # back to opening the release page.
    installer_url: Optional[str] = None
    installer_name: Optional[str] = None


def _parse_version(v: str) -> tuple[int, ...]:
    v = v.strip().lstrip("v")
    parts = re.findall(r"\d+", v)
    return tuple(int(p) for p in parts) if parts else (0,)


def check(timeout: float = 5.0) -> Optional[UpdateInfo]:
    """Return UpdateInfo if a newer release is available, else None.

    Returns None on any network/parse error too — silently fails so a
    flaky network never gates app startup.
    """
    try:
        r = requests.get(
            GITHUB_LATEST,
            headers={"Accept": "application/vnd.github+json"},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        tag = data.get("tag_name") or ""
        url = data.get("html_url") or ""
        body = (data.get("body") or "").strip()
        assets = data.get("assets") or []
    except requests.RequestException as e:
        _log.debug("Update check failed: %s", e)
        return None
    except (KeyError, ValueError) as e:
        _log.debug("Update check parse error: %s", e)
        return None

    if not tag:
        return None
    latest = _parse_version(tag)
    current = _parse_version(__version__)
    available = latest > current
    if available:
        _log.info("Update available: %s → %s", __version__, tag)
    installer_url, installer_name = _pick_installer_asset(assets)
    return UpdateInfo(
        current=__version__,
        latest=tag,
        release_url=url,
        body=body,
        available=available,
        installer_url=installer_url,
        installer_name=installer_name,
    )


def _pick_installer_asset(assets: list) -> tuple[Optional[str], Optional[str]]:
    """Pick the right release asset for the current platform.

    Windows: SynapseVoice-Setup-X.Y.Z.exe (NSIS installer — handles the
        running-process kill + reinstall + relaunch).
    Linux:   SynapseVoice-x86_64.AppImage (self-contained binary; we
        replace the running file in-place + re-exec).
    """
    is_win = sys.platform == "win32"
    for a in assets:
        name = (a.get("name") or "").strip()
        url = a.get("browser_download_url") or ""
        lower = name.lower()
        if is_win and lower.endswith(".exe") and "setup" in lower:
            return url, name
        if (not is_win) and lower.endswith(".appimage"):
            return url, name
    return None, None


_ALLOWED_DL_HOSTS = (
    "github.com",
    "objects.githubusercontent.com",  # GitHub Releases CDN
    "github-releases.githubusercontent.com",
)


def _is_allowed_download_url(url: str) -> bool:
    """Whitelist GitHub-hosted release downloads. Defense-in-depth so a
    compromised release.json or upstream redirect can't trick us into
    fetching arbitrary URLs and handing them to ShellExecute as admin."""
    try:
        from urllib.parse import urlparse

        u = urlparse(url)
        if u.scheme != "https":
            return False
        host = (u.hostname or "").lower()
        return any(host == h or host.endswith("." + h) for h in _ALLOWED_DL_HOSTS)
    except Exception:
        return False


def download_installer(
    url: str,
    target_dir: Optional[Path] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    timeout: float = 30.0,
) -> Path:
    """Stream-download the installer to disk. Calls progress_cb(bytes, total)
    on every chunk if total size is known. Returns the saved path."""
    if not _is_allowed_download_url(url):
        raise ValueError(
            f"Refusing to download from non-GitHub host: {url!r}. "
            "Update aborted as a safety check."
        )
    if target_dir is None:
        target_dir = Path(tempfile.gettempdir())
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / (Path(url).name or "synapse-voice-update.bin")

    _log.info("Downloading update from %s → %s", url, target)
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        downloaded = 0
        with target.open("wb") as f:
            for chunk in r.iter_content(chunk_size=128 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb and total:
                    try:
                        progress_cb(downloaded, total)
                    except Exception:
                        pass  # progress UI errors mustn't kill the download
    return target


def launch_installer_and_quit(installer: Path) -> None:
    """Spawn the installer detached so it survives our exit, then signal
    the caller to quit. The NSIS installer auto-kills any running
    synapse-voice.exe and re-launches it after install. AppImage path
    just chmod+x and exec.

    Win specifics: our NSIS installer is built with RequestExecutionLevel
    admin, so it has to be launched through ShellExecuteW with verb="runas"
    to trigger the UAC prompt. Plain subprocess.Popen fails with WinError
    740 ("ERROR_ELEVATION_REQUIRED") — that's exactly the symptom TJ hit
    on his first auto-update attempt.
    """
    if sys.platform == "win32":
        import ctypes

        SW_SHOWNORMAL = 1
        _log.info("Launching installer (UAC): %s", installer)
        # ShellExecuteW returns an HINSTANCE; >32 means success. Lower
        # values are error codes (5 = access denied, 31 = no app
        # associated, etc).
        rc = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            str(installer),
            None,
            None,
            SW_SHOWNORMAL,
        )
        if rc <= 32:
            # Fall back to a non-elevated run — works for users who tweaked
            # the installer to not require admin, or for the rare case
            # where ShellExecute itself fails (cancelled UAC returns
            # SE_ERR_ACCESSDENIED == 5 — that one we surface as failure).
            if rc == 5:
                raise RuntimeError(
                    "User cancelled the elevation prompt — update aborted."
                )
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            subprocess.Popen(
                [str(installer)],
                close_fds=True,
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            )
        return
    # Linux: AppImage replace-and-restart. Caller is responsible for
    # putting the new file into place — we just chmod + spawn.
    try:
        os.chmod(installer, 0o755)
    except Exception:
        pass
    _log.info("Launching AppImage: %s", installer)
    subprocess.Popen([str(installer)], close_fds=True)
