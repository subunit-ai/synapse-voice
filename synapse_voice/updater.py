"""Auto-update checker — polls GitHub Releases for a newer version.

Network call is best-effort and timeouts quickly so app startup isn't
delayed if GitHub is slow. v0.3.4: result drives an in-app download +
installer launch instead of just opening the release page in a browser.
v0.3.14: SHA-256 verification, redirect host validation, private temp
directory, clipboard restore — all per Codex audit findings.
"""
from __future__ import annotations

import hashlib
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
    # v0.3.14: SHA-256 hash of the installer. Parsed out of the release
    # body (CI workflow appends a `## SHA256` section). None for older
    # releases that pre-date the workflow change — those updates skip
    # hash verification with a warning.
    installer_sha256: Optional[str] = None


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
    installer_sha256 = _extract_installer_hash(body, installer_name)
    return UpdateInfo(
        current=__version__,
        latest=tag,
        release_url=url,
        body=body,
        available=available,
        installer_url=installer_url,
        installer_name=installer_name,
        installer_sha256=installer_sha256,
    )


def _extract_installer_hash(body: str, installer_name: Optional[str]) -> Optional[str]:
    """Pull the SHA-256 of the installer out of the release body. CI
    appends a `## SHA256` section like:

        ## SHA256
        - SynapseVoice-Setup-0.3.14.exe: `abcd1234...`
        - SynapseVoice-x86_64.AppImage: `9876fedc...`

    Returns the lowercase hex hash for `installer_name`, or None if the
    body has no hash section yet (older releases pre-CI-update)."""
    if not body or not installer_name:
        return None
    pattern = re.compile(
        r"`?" + re.escape(installer_name) + r"`?\s*[:|]\s*`?([a-fA-F0-9]{64})`?",
        re.MULTILINE,
    )
    m = pattern.search(body)
    if m:
        return m.group(1).lower()
    return None


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
    expected_sha256: Optional[str] = None,
) -> Path:
    """Stream-download the installer to disk. Verifies the host of every
    redirect-hop, writes to a private 0700 temp directory, computes the
    SHA-256 while streaming, and rejects the file if it doesn't match
    `expected_sha256` (when provided).

    Calls progress_cb(bytes, total) on every chunk if total size is
    known. Returns the saved path on success.
    """
    if not _is_allowed_download_url(url):
        raise ValueError(
            f"Refusing to download from non-GitHub host: {url!r}. "
            "Update aborted as a safety check."
        )
    # Codex-finding (Should): the original URL allowlist didn't validate
    # the redirect chain. Resolve redirects manually so every hop is
    # checked against the same allowlist before we follow it.
    resolved_url, hop_count = _resolve_redirects(url, max_hops=5, timeout=timeout)
    if not _is_allowed_download_url(resolved_url):
        raise ValueError(
            f"Refusing to follow redirect to non-GitHub host: {resolved_url!r}. "
            "Update aborted as a safety check."
        )
    if hop_count:
        _log.info("Update URL resolved through %d redirect(s) → %s", hop_count, resolved_url)

    # Codex-finding (Should): write to a private 0700 temp dir we just
    # created, not the world-readable shared tmp where another local
    # process could symlink-race the path before we ShellExecute it.
    if target_dir is None:
        target_dir = Path(tempfile.mkdtemp(prefix="synapse-voice-update-"))
    else:
        target_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(target_dir, 0o700)
    except OSError:
        pass  # filesystem doesn't support modes (Win FAT etc.) — tolerable
    target = target_dir / (Path(resolved_url).name or "synapse-voice-update.bin")

    _log.info("Downloading update from %s → %s", resolved_url, target)
    hasher = hashlib.sha256()
    with requests.get(resolved_url, stream=True, timeout=timeout, allow_redirects=False) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        downloaded = 0
        with target.open("wb") as f:
            for chunk in r.iter_content(chunk_size=128 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                hasher.update(chunk)
                downloaded += len(chunk)
                if progress_cb and total:
                    try:
                        progress_cb(downloaded, total)
                    except Exception:
                        pass  # progress UI errors mustn't kill the download
    actual = hasher.hexdigest()
    _log.info("Update SHA-256: %s", actual)

    # Codex-finding (Must): verify hash before we hand the file to
    # ShellExecute as admin. If the release didn't ship a hash (older
    # release pre-CI-update), log a warning but continue — those still
    # benefit from the host allowlist + UAC prompt.
    if expected_sha256:
        if actual.lower() != expected_sha256.lower():
            try:
                target.unlink()
            except OSError:
                pass
            raise ValueError(
                f"Installer hash mismatch — expected {expected_sha256}, "
                f"got {actual}. File deleted, update aborted."
            )
        _log.info("Update hash verified ✓")
    else:
        _log.warning(
            "No expected SHA-256 supplied — proceeding without hash verification. "
            "Older release without hash in body."
        )
    return target


def _resolve_redirects(url: str, max_hops: int = 5, timeout: float = 30.0) -> tuple[str, int]:
    """Walk the redirect chain HEAD-by-HEAD, validating each Location
    header against the allowlist. Returns (final_url, hop_count) on
    success, raises ValueError if any hop points outside the allowlist."""
    current = url
    for hop in range(max_hops):
        try:
            r = requests.head(current, allow_redirects=False, timeout=timeout)
        except requests.RequestException:
            # HEAD failed — the GET will fail too with a clearer error.
            return current, hop
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("Location")
            if not loc:
                return current, hop
            # Resolve relative redirects against the current URL
            from urllib.parse import urljoin

            current = urljoin(current, loc)
            if not _is_allowed_download_url(current):
                raise ValueError(
                    f"Redirect chain leads outside the GitHub allowlist: {current!r}"
                )
        else:
            return current, hop
    return current, max_hops


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
