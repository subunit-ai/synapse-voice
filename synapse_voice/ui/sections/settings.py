"""v0.10.0 Hub: Settings section.

Inline replacement for the legacy SettingsDialog modal. We use a
horizontal sub-tab strip + a content QStackedWidget so each panel
swaps inline without opening a new window. The panels themselves
re-use the legacy SettingsDialog's build methods (general,
transcription, overlay, account) so we don't fork their widget
trees — Phase 2 is layout-level only, panel internals stay the same.
"""
from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...config import Config


CYAN = "#06b6d4"
WHITE = "#e2e8f0"
WHITE_DIM = "#94a3b8"
SURFACE = "#1e293b"


# Sub-tab definitions: (key, label, dialog-build-method-name)
SUBTABS: list[tuple[str, str, str]] = [
    ("general",       "Allgemein",     "_build_general_tab"),
    ("transcription", "Transkription", "_build_transcription_tab"),
    ("overlay",       "Overlay",       "_build_overlay_tab"),
    ("account",       "Account",       "_build_account_tab"),
]


class _SubTabButton(QPushButton):
    def __init__(self, label: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(label, parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(36)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent; color: {WHITE_DIM};
                border: none; border-bottom: 2px solid transparent;
                padding: 4px 18px; font-size: 13px; font-weight: 600;
            }}
            QPushButton:hover {{ color: {WHITE}; }}
            QPushButton:checked {{
                color: {CYAN}; border-bottom: 2px solid {CYAN};
            }}
            """
        )


class SettingsSection(QWidget):
    """Inline settings — sub-tabbed pages with a Save bar at the bottom."""

    def __init__(
        self,
        config: Config,
        on_apply: Callable[[], None],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self._on_apply = on_apply

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(14)

        # Heading
        h = QLabel("Einstellungen")
        h.setStyleSheet(f"color: {WHITE}; font-size: 22px; font-weight: 700;")
        outer.addWidget(h)
        sub = QLabel("Änderungen werden mit „Speichern“ unten wirksam.")
        sub.setStyleSheet(f"color: {WHITE_DIM}; font-size: 13px;")
        outer.addWidget(sub)

        # Sub-tabs strip
        tab_row = QHBoxLayout()
        tab_row.setSpacing(0)
        self._tab_buttons: dict[str, _SubTabButton] = {}
        for key, label, _build in SUBTABS:
            btn = _SubTabButton(label)
            btn.clicked.connect(lambda _checked=False, k=key: self._select(k))
            self._tab_buttons[key] = btn
            tab_row.addWidget(btn)
        tab_row.addStretch()
        outer.addLayout(tab_row)

        # Content stack — instantiate the legacy SettingsDialog ONCE
        # but with the dialog visibility off, so we can pull individual
        # tab widgets out of it and re-parent them into our stack. This
        # avoids duplicating ~900 lines of panel-building code.
        from ..settings import SettingsDialog
        self._dialog_host = SettingsDialog(config=config)
        self._dialog_host.setVisible(False)
        # The dialog already built every tab in its own QTabWidget — we
        # just need to reach into that and lift each panel.
        self._content = QStackedWidget()
        outer.addWidget(self._content, 1)

        # Populate the stack with the panels we need. The dialog's
        # `tabs` attribute is the QTabWidget; we walk its children and
        # match by index using the tab labels we already know.
        for key, label, _build in SUBTABS:
            panel = self._lift_panel_by_label(label)
            if panel is None:
                # Fallback — tiny missing-panel placeholder so the user
                # never lands on a blank screen.
                panel = QLabel(f"[{label}] Panel nicht verfügbar.")
                panel.setStyleSheet(f"color: {WHITE_DIM}; padding: 24px;")
            self._content.addWidget(panel)

        # Save bar
        save_row = QHBoxLayout()
        save_row.addStretch()
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"color: {CYAN}; font-size: 12px;")
        save_row.addWidget(self._status_lbl)
        save_btn = QPushButton("Speichern")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setMinimumHeight(40)
        save_btn.setMinimumWidth(140)
        save_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {CYAN}; color: #031426;
                border: none; border-radius: 8px;
                padding: 8px 22px; font-weight: 700;
            }}
            QPushButton:hover {{ background: #22d3ee; }}
            """
        )
        save_btn.clicked.connect(self._save)
        save_row.addWidget(save_btn)
        outer.addLayout(save_row)

        # Default to first tab
        self._select("general")

    # ── panel lifting ───────────────────────────────────────────────────
    def _lift_panel_by_label(self, label: str) -> Optional[QWidget]:
        tabs = getattr(self._dialog_host, "tabs", None)
        if tabs is None:
            return None
        for i in range(tabs.count()):
            if tabs.tabText(i).strip().lower() == label.lower():
                w = tabs.widget(i)
                # Re-parent so removing from the tab widget doesn't
                # delete it; the stack now owns it.
                w.setParent(None)
                # v0.10.8: re-apply the dark stylesheet to the lifted
                # panel. DARK_QSS was originally scoped on the SettingsDialog
                # via `QDialog` selector; once we lift a child widget out
                # of that QDialog parent the cascade breaks and the panel
                # falls back to system theme (= white on Win11). We give
                # the panel objectName "tabPage" so the existing
                # `QWidget#tabPage` selector in DARK_QSS matches and
                # paints it NIGHT + applies all child rules.
                from ..settings import DARK_QSS
                w.setObjectName("tabPage")
                w.setStyleSheet(DARK_QSS)
                return w
        return None

    def _select(self, key: str) -> None:
        for k, btn in self._tab_buttons.items():
            btn.setChecked(k == key)
        idx = next((i for i, (k, *_rest) in enumerate(SUBTABS) if k == key), 0)
        self._content.setCurrentIndex(idx)

    def _save(self) -> None:
        # Delegate to the SettingsDialog's `apply_to` (it knows how to
        # harvest values from every widget tree we're showing).
        try:
            self._dialog_host.apply_to(self.config)
            self.config.save()
            self._status_lbl.setText("Gespeichert ✓")
            QTimer.singleShot(2200, lambda: self._status_lbl.setText(""))
            if self._on_apply is not None:
                self._on_apply()
        except Exception as exc:  # never crash settings save
            self._status_lbl.setText(f"Fehler: {exc}")
