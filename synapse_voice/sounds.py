"""Subtle UI sound feedback — record-start ping + transcribe-done pop.

Uses Qt's QSoundEffect for low-latency playback (loads + caches WAV in
memory, ~5-10ms trigger time vs QMediaPlayer's ~100-200ms). Files ship
inside synapse_voice/sounds/ and are resolved through the same
PyInstaller-aware loader the brand logo uses.

Volume comes from `config.sound_volume` (0.0–1.0). Whole feature
gate-kept by `config.sound_enabled`.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QUrl
from PyQt6.QtMultimedia import QSoundEffect

from .logger import get as _get_logger

_log = _get_logger(__name__)

# Cached effect-per-name so re-plays don't re-load the WAV from disk.
_CACHE: dict[str, QSoundEffect] = {}


def _candidates(filename: str) -> list[Path]:
    """Where to look for the WAV — same pattern as ui.widgets._logo_candidates.
    Covers dev runs (repo-relative), pip-installed packages, and
    PyInstaller bundles (via sys._MEIPASS)."""
    here = Path(__file__).resolve().parent
    paths = [
        here / "sounds" / filename,
        here.parent / "sounds" / filename,
    ]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        paths.append(Path(meipass) / "synapse_voice" / "sounds" / filename)
        paths.append(Path(meipass) / "sounds" / filename)
    return paths


def _resolve(filename: str) -> Optional[Path]:
    for p in _candidates(filename):
        if p.is_file():
            return p
    return None


def play(name: str, *, volume: float = 0.7) -> None:
    """Fire-and-forget play of a named effect. `name` is the file stem
    inside synapse_voice/sounds/ (e.g. "start" → start.wav).

    Silent on missing file or QtMultimedia init failure — sound is a
    nice-to-have, never block the main flow.
    """
    try:
        effect = _CACHE.get(name)
        if effect is None:
            path = _resolve(f"{name}.wav")
            if path is None:
                _log.debug("sound %s.wav not found", name)
                return
            effect = QSoundEffect()
            effect.setSource(QUrl.fromLocalFile(str(path)))
            _CACHE[name] = effect
        effect.setVolume(max(0.0, min(1.0, volume)))
        effect.play()
    except Exception as e:
        _log.debug("sound play failed (%s): %s", name, e)
