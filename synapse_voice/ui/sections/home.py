"""v0.10.0 Hub: Home section.

Ports the hero + stats + recent-transcriptions strip from the legacy
MainWindow into a flat QWidget that the Hub can swap into its content
pane. We keep the same widgets (LocalCloudSwitch, StatCard…) so the
look stays consistent and we don't rebuild visual primitives — just
the surrounding layout shell changes."""
from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...config import Config


CYAN = "#06b6d4"
WHITE = "#e2e8f0"
WHITE_DIM = "#94a3b8"
SURFACE = "#1e293b"
CARD_RADIUS = 12


def _stat_card(label: str, value: str) -> tuple[QFrame, QLabel]:
    """Return (card_widget, value_label) so callers can update the
    value cheaply on refresh without re-walking the widget tree."""
    frame = QFrame()
    frame.setObjectName("statCard")
    frame.setStyleSheet(
        f"QFrame#statCard {{ background: {SURFACE}; border-radius: {CARD_RADIUS}px; }}"
    )
    box = QVBoxLayout(frame)
    box.setContentsMargins(20, 16, 20, 16)
    box.setSpacing(4)
    lbl = QLabel(label)
    lbl.setStyleSheet(f"color: {WHITE_DIM}; font-size: 11px; letter-spacing: 1.4px;")
    val = QLabel(value)
    val.setObjectName("statValue")
    val.setStyleSheet(f"color: {WHITE}; font-size: 26px; font-weight: 700;")
    box.addWidget(lbl)
    box.addWidget(val)
    return frame, val


class HomeSection(QWidget):
    """Top-level dashboard view.

    Receives callbacks from the Hub so it can trigger start-meeting,
    open-history (navigates the sidebar instead of opening a window),
    and read live state from the Config + the active main controller.
    """

    def __init__(
        self,
        config: Config,
        on_start_meeting: Optional[Callable[[], None]] = None,
        on_nav: Optional[Callable[[str], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self._on_start_meeting = on_start_meeting
        self._on_nav = on_nav or (lambda _key: None)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 28, 28, 28)
        outer.setSpacing(22)

        # Greeting strip ─────────────────────────────────────────────
        greet_row = QHBoxLayout()
        greet_row.setSpacing(12)
        title = QLabel("Willkommen zurück")
        title.setStyleSheet(f"color: {WHITE}; font-size: 24px; font-weight: 700;")
        subtitle = QLabel("Drücke deinen Hotkey um aufzunehmen.")
        subtitle.setStyleSheet(f"color: {WHITE_DIM}; font-size: 14px;")
        gtitle_box = QVBoxLayout()
        gtitle_box.setSpacing(2)
        gtitle_box.addWidget(title)
        gtitle_box.addWidget(subtitle)
        greet_row.addLayout(gtitle_box, 1)

        if on_start_meeting is not None:
            start_btn = QPushButton("🎙   Meeting starten")
            start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            start_btn.setMinimumHeight(44)
            start_btn.setMinimumWidth(220)
            start_btn.setStyleSheet(
                f"""
                QPushButton {{
                    background: {CYAN}; color: #031426;
                    border: none; border-radius: 22px;
                    font-size: 15px; font-weight: 700; padding: 0 22px;
                }}
                QPushButton:hover {{ background: #22d3ee; }}
                QPushButton:pressed {{ background: #0e7490; color: #e0f2fe; }}
                """
            )
            start_btn.clicked.connect(lambda: on_start_meeting())
            greet_row.addWidget(start_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        outer.addLayout(greet_row)

        # Stat strip ─────────────────────────────────────────────────
        stats_row = QHBoxLayout()
        stats_row.setSpacing(14)
        c1, self.stat_count_val = _stat_card("TRANSCRIBED", str(config.total_transcriptions))
        c2, self.stat_audio_val = _stat_card("AUDIO PROCESSED", _fmt_duration(config.total_audio_seconds))
        c3, self.stat_saved_val = _stat_card("TIME SAVED",      _fmt_duration(config.total_audio_seconds * 4))
        stats_row.addWidget(c1, 1)
        stats_row.addWidget(c2, 1)
        stats_row.addWidget(c3, 1)
        outer.addLayout(stats_row)

        # Mode quick-status card ─────────────────────────────────────
        outer.addWidget(self._build_mode_card())

        # Recent transcriptions strip (top 5) ─────────────────────────
        outer.addWidget(self._build_recent_card())

        outer.addStretch(1)

    # ── refresh hook (called by Hub on a timer) ────────────────────────
    def refresh(self) -> None:
        self.stat_count_val.setText(str(self.config.total_transcriptions))
        secs = self.config.total_audio_seconds
        self.stat_audio_val.setText(_fmt_duration(secs))
        self.stat_saved_val.setText(_fmt_duration(secs * 4))

    def _build_mode_card(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("modeCard")
        frame.setStyleSheet(
            f"QFrame#modeCard {{ background: {SURFACE}; border-radius: {CARD_RADIUS}px; }}"
        )
        row = QHBoxLayout(frame)
        row.setContentsMargins(22, 18, 22, 18)
        row.setSpacing(14)

        box = QVBoxLayout()
        box.setSpacing(2)
        head = QLabel("Aktueller Modus")
        head.setStyleSheet(f"color: {WHITE_DIM}; font-size: 11px; letter-spacing: 1.4px;")
        provider = self.config.mode
        quality = (getattr(self.config, "cloud_quality_mode", "quality") or "quality").upper()
        line = "LOKAL" if provider == "local" else f"CLOUD · {provider.upper()} · {quality}"
        val = QLabel(line)
        val.setStyleSheet(f"color: {WHITE}; font-size: 18px; font-weight: 600;")
        box.addWidget(head)
        box.addWidget(val)
        row.addLayout(box, 1)

        cta = QPushButton("Anpassen → Settings")
        cta.setCursor(Qt.CursorShape.PointingHandCursor)
        cta.setMinimumHeight(36)
        cta.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent; color: {CYAN};
                border: 1px solid {CYAN}; border-radius: 8px;
                padding: 6px 16px; font-weight: 600;
            }}
            QPushButton:hover {{ background: rgba(6,182,212,30); }}
            """
        )
        cta.clicked.connect(lambda: self._on_nav("settings"))
        row.addWidget(cta, 0, Qt.AlignmentFlag.AlignVCenter)
        return frame

    def _build_recent_card(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("recentCard")
        frame.setStyleSheet(
            f"QFrame#recentCard {{ background: {SURFACE}; border-radius: {CARD_RADIUS}px; }}"
        )
        col = QVBoxLayout(frame)
        col.setContentsMargins(22, 18, 22, 18)
        col.setSpacing(10)

        head_row = QHBoxLayout()
        head = QLabel("Letzte Transkriptionen")
        head.setStyleSheet(f"color: {WHITE_DIM}; font-size: 11px; letter-spacing: 1.4px;")
        head_row.addWidget(head)
        head_row.addStretch()
        link = QPushButton("Verlauf öffnen →")
        link.setCursor(Qt.CursorShape.PointingHandCursor)
        link.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {CYAN}; border: none; font-weight: 600; }}"
            f"QPushButton:hover {{ color: #22d3ee; }}"
        )
        link.clicked.connect(lambda: self._on_nav("history"))
        head_row.addWidget(link)
        col.addLayout(head_row)

        history = getattr(self.config, "history", None) or []
        latest = history[-5:][::-1] if history else []
        if not latest:
            empty = QLabel("Noch keine Aufnahmen — drücke deinen Hotkey um die erste zu starten.")
            empty.setStyleSheet(f"color: {WHITE_DIM}; font-size: 13px; padding: 6px 0;")
            empty.setWordWrap(True)
            col.addWidget(empty)
            return frame

        for entry in latest:
            text = (entry.get("text") or "").strip()
            ts = (entry.get("ts") or "").replace("T", " ")[:16]
            mode = entry.get("mode", "")
            line = QLabel(
                f"<span style='color:{WHITE};font-size:13px;'>{(text[:120] + '…') if len(text) > 120 else text}</span>"
                f"<br><span style='color:{WHITE_DIM};font-size:11px;'>{ts} · {mode}</span>"
            )
            line.setTextFormat(Qt.TextFormat.RichText)
            line.setWordWrap(True)
            col.addWidget(line)

        return frame


def _fmt_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds or 0.0))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    if h:
        return f"{h}h {m}m"
    return f"{m}m"
