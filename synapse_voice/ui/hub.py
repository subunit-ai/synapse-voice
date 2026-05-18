"""v0.10.0 Hub — the new single-window main UI.

Replaces the legacy `MainWindow` + separate History/Meetings/Settings
dialogs with one cohesive window:

    ┌─ Header (Brand · Plan-Badge · Avatar) ──────────────────┐
    ├─────────┬─────────────────────────────────────────────────┤
    │ Sidebar │  Content pane (swaps per nav selection)         │
    │ (200px) │                                                  │
    │ Home    │                                                  │
    │ Verlauf │                                                  │
    │ Meet.   │                                                  │
    │ Voc.    │                                                  │
    │ Settings│                                                  │
    │ Hilfe   │                                                  │
    └─────────┴─────────────────────────────────────────────────┘

The Hub doesn't own any business logic — it forwards callbacks (open
settings, start meeting, change mode, ...) up to the main App so the
recording pipeline + tray + orb keep working unchanged. This keeps the
diff manageable: only the UI layer flips, the rest of the app is
backward-compatible.

Phase 1: Hub shell + Header + Sidebar + Home section. Other sections
render as placeholders that punt back to the legacy dialogs.
"""
from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from .hub_header import HubHeader
from .hub_sidebar import HubSidebar
from .sections.home import HomeSection
from .sections.placeholder import PlaceholderSection


class Hub(QMainWindow):
    """Single-window Sonar main UI."""

    def __init__(
        self,
        config: Config,
        on_change_mode: Callable[[str], None],
        on_open_settings: Callable[[], None],
        on_open_history: Callable[[], None],
        on_open_meetings: Optional[Callable[[], None]] = None,
        on_start_meeting: Optional[Callable[[], None]] = None,
        on_quit: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__()
        self.config = config
        self._on_change_mode = on_change_mode
        self._on_open_settings = on_open_settings
        self._on_open_history = on_open_history
        self._on_open_meetings = on_open_meetings
        self._on_start_meeting = on_start_meeting
        self._on_quit = on_quit

        self.setWindowTitle("Sonar")
        self.resize(1280, 800)
        self.setMinimumSize(1024, 640)
        self.setStyleSheet(
            "QMainWindow { background: #0a1424; }"
            "QWidget#hubContentArea { background: #0a1424; }"
        )

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Header
        self.header = HubHeader(self)
        self.header.profile_clicked.connect(lambda: self.sidebar.select("settings"))
        root_layout.addWidget(self.header)

        # Body: sidebar + content pane
        body = QWidget()
        body.setObjectName("hubContentArea")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)

        from PyQt6.QtWidgets import QHBoxLayout
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.sidebar = HubSidebar(self)
        self.sidebar.section_changed.connect(self._on_section_changed)
        row.addWidget(self.sidebar)

        self.content = QStackedWidget(self)
        self.content.setObjectName("hubContent")
        row.addWidget(self.content, 1)

        body_layout.addLayout(row)
        root_layout.addWidget(body, 1)

        self.setCentralWidget(root)

        # Build sections (Phase 1: Home + placeholders for the rest).
        self._sections: dict[str, QWidget] = {}
        self._build_sections()

        # PlanBadge in main.py wires its existing refresh path through
        # the legacy MainWindow; until that's ported we expose the Hub's
        # plan_badge attribute so the existing code keeps working.
        self.plan_badge = self.header.plan_badge

        # Periodic stat refresh — Home reads transcription totals from
        # Config which mutates after every dictation.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1500)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start()

    # ── sections ────────────────────────────────────────────────────────
    def _build_sections(self) -> None:
        self._sections["home"] = HomeSection(
            self.config,
            on_start_meeting=self._on_start_meeting,
            on_nav=self.sidebar.select,
        )
        self._sections["history"] = PlaceholderSection(
            "Verlauf",
            "Inline-Verlauf kommt in v0.10.0 Phase 3 — bis dahin öffnet "
            "der Verlauf im klassischen Fenster.",
            cta_label="Verlauf öffnen",
            cta_callback=self._on_open_history,
        )
        meetings_cb = self._on_open_meetings or (lambda: None)
        self._sections["meetings"] = PlaceholderSection(
            "Meetings",
            "Inline-Meetings-Ansicht kommt in v0.10.0 Phase 3 — bis dahin "
            "öffnen die Meeting-Notizen im klassischen Fenster.",
            cta_label="Meetings öffnen",
            cta_callback=meetings_cb,
        )
        self._sections["vocabulary"] = PlaceholderSection(
            "Vocabulary",
            "Die Vocabulary-Tabelle bekommt in Phase 3 eine eigene Sektion. "
            "Aktuell ist sie im Settings-Dialog → Tab Vocabulary erreichbar.",
            cta_label="Settings → Vocabulary öffnen",
            cta_callback=self._on_open_settings,
        )
        self._sections["settings"] = PlaceholderSection(
            "Settings",
            "Inline-Settings kommen in v0.10.0 Phase 2. Bis dahin öffnet "
            "der klassische Settings-Dialog.",
            cta_label="Settings öffnen",
            cta_callback=self._on_open_settings,
        )
        self._sections["help"] = PlaceholderSection(
            "Hilfe",
            "Doku, Diagnose und Lizenzhinweise — kommt in Phase 3.",
        )

        for key, widget in self._sections.items():
            self.content.addWidget(widget)

        self.content.setCurrentWidget(self._sections["home"])

    def _on_section_changed(self, key: str) -> None:
        widget = self._sections.get(key)
        if widget is not None:
            self.content.setCurrentWidget(widget)

    # ── public API (mirrors legacy MainWindow surface) ──────────────────
    def refresh(self) -> None:
        home = self._sections.get("home")
        if isinstance(home, HomeSection):
            home.refresh()

    def refresh_mode(self) -> None:
        """Settings-Dialog calls this after the user changes the mode.
        We rebuild Home so the mode-card reflects the new state."""
        home_old = self._sections.get("home")
        if home_old is None:
            return
        idx = self.content.indexOf(home_old)
        new_home = HomeSection(
            self.config,
            on_start_meeting=self._on_start_meeting,
            on_nav=self.sidebar.select,
        )
        self._sections["home"] = new_home
        self.content.insertWidget(idx, new_home)
        self.content.removeWidget(home_old)
        home_old.deleteLater()
        if self.sidebar.current() == "home":
            self.content.setCurrentWidget(new_home)

    def set_status(self, label: str, color: str = "#06b6d4") -> None:
        # No-op kept for legacy callsite compatibility — Hub mirrors
        # MainWindow's no-op behaviour (status lives in tray + orb).
        return

    def closeEvent(self, event) -> None:
        # Hide-to-tray instead of quitting, matching the legacy
        # MainWindow behaviour. Quit goes through the tray Quit menu.
        event.ignore()
        self.hide()
