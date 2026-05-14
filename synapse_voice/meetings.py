"""Persistent storage for long-form meeting transcripts.

Meetings are stored as JSON files under
``CONFIG_DIR / meetings / <uuid>.json`` so they survive across app sessions and
can be browsed in the Meetings tab.

A "meeting" here is any transcription whose duration meets or exceeds
``Config.long_form_threshold_seconds`` (default 240 s). Shorter dictations are
not persisted — they belong to the regular history list.
"""
from __future__ import annotations

import json
import logging
import time
import uuid as _uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .config import CONFIG_DIR

_log = logging.getLogger(__name__)

MEETINGS_DIR = CONFIG_DIR / "meetings"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Meeting:
    id: str
    created_at_iso: str
    duration_seconds: float
    language: str
    source: str
    window_title: str
    transcript_raw: str
    cleanup_versions: dict = field(default_factory=dict)
    tags: list = field(default_factory=list)
    extracted_tasks_count: int = 0
    extracted_decisions_count: int = 0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Meeting":
        return cls(
            id=str(data.get("id", "")),
            created_at_iso=str(data.get("created_at_iso", "")),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
            language=str(data.get("language", "")),
            source=str(data.get("source", "")),
            window_title=str(data.get("window_title", "")),
            transcript_raw=str(data.get("transcript_raw", "")),
            cleanup_versions=dict(data.get("cleanup_versions") or {}),
            tags=list(data.get("tags") or []),
            extracted_tasks_count=int(data.get("extracted_tasks_count", 0)),
            extracted_decisions_count=int(data.get("extracted_decisions_count", 0)),
            metadata=dict(data.get("metadata") or {}),
        )

    @property
    def created_at_local_str(self) -> str:
        """Render created_at_iso as a friendly local-time string."""
        try:
            dt = datetime.strptime(self.created_at_iso, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
            local = dt.astimezone()
            return local.strftime("%a %d.%m.%Y %H:%M")
        except Exception:
            return self.created_at_iso

    @property
    def duration_str(self) -> str:
        sec = int(round(self.duration_seconds))
        if sec < 60:
            return f"{sec}s"
        m, s = divmod(sec, 60)
        if m < 60:
            return f"{m}:{s:02d}"
        h, m = divmod(m, 60)
        return f"{h}h{m:02d}"

    @property
    def title(self) -> str:
        """Best-effort human title — first non-trivial line, otherwise source."""
        for raw_line in (self.transcript_raw or "").splitlines():
            line = raw_line.strip()
            if len(line) >= 8:
                return line[:140]
        if self.window_title:
            return self.window_title[:140]
        return f"{self.source or 'Meeting'} ({self.duration_str})"


class MeetingsStore:
    """File-backed store of meetings.

    Thread-safety is not required — all callers should hop to the Qt main
    thread before mutating.
    """

    def __init__(self, base_dir: Path = MEETINGS_DIR) -> None:
        self._base_dir = base_dir
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, meeting_id: str) -> Path:
        return self._base_dir / f"{meeting_id}.json"

    def create(
        self,
        *,
        transcript_raw: str,
        duration_seconds: float,
        language: str,
        source: str,
        window_title: str = "",
        cleanup_versions: dict | None = None,
        metadata: dict | None = None,
    ) -> Meeting:
        meeting = Meeting(
            id=str(_uuid.uuid4()),
            created_at_iso=_now_iso(),
            duration_seconds=float(duration_seconds),
            language=language or "",
            source=source or "Microphone",
            window_title=window_title or "",
            transcript_raw=transcript_raw or "",
            cleanup_versions=cleanup_versions or {},
            metadata=metadata or {},
        )
        self._write(meeting)
        return meeting

    def update(self, meeting: Meeting) -> None:
        meeting.metadata.setdefault("updated_at", time.time())
        self._write(meeting)

    def list_all(self, *, limit: int | None = None) -> list[Meeting]:
        meetings: list[Meeting] = []
        for p in sorted(self._base_dir.glob("*.json"), reverse=True):
            try:
                meetings.append(Meeting.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            except Exception as e:
                _log.warning("Skipping corrupt meeting file %s: %s", p, e)
            if limit is not None and len(meetings) >= limit:
                break
        # Sort newest first by created_at_iso
        meetings.sort(key=lambda m: m.created_at_iso, reverse=True)
        return meetings

    def get(self, meeting_id: str) -> Meeting | None:
        p = self.path_for(meeting_id)
        if not p.exists():
            return None
        try:
            return Meeting.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception as e:
            _log.warning("Failed to read meeting %s: %s", meeting_id, e)
            return None

    def delete(self, meeting_id: str) -> bool:
        p = self.path_for(meeting_id)
        if not p.exists():
            return False
        try:
            p.unlink()
            return True
        except Exception as e:
            _log.warning("Failed to delete meeting %s: %s", meeting_id, e)
            return False

    def search(self, query: str) -> list[Meeting]:
        q = (query or "").strip().lower()
        if not q:
            return self.list_all()
        results: list[Meeting] = []
        for m in self.list_all():
            haystack = " ".join([
                m.title.lower(),
                m.transcript_raw.lower(),
                m.window_title.lower(),
                " ".join(str(v).lower() for v in (m.cleanup_versions or {}).values()),
            ])
            if q in haystack:
                results.append(m)
        return results

    def _write(self, meeting: Meeting) -> None:
        p = self.path_for(meeting.id)
        tmp = p.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(meeting.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(p)
        except Exception as e:
            _log.error("Failed to write meeting %s: %s", meeting.id, e)
            raise


def detect_source_from_window_title(window_title: str | None) -> str:
    """Heuristic source-tagging based on the focused window when recording started."""
    if not window_title:
        return "Microphone"
    title = window_title.lower()
    if "zoom" in title:
        return "Zoom"
    if "microsoft teams" in title or "teams" in title:
        return "Microsoft Teams"
    if "google meet" in title or "meet.google" in title:
        return "Google Meet"
    if "discord" in title:
        return "Discord"
    if "skype" in title:
        return "Skype"
    return "Microphone"
