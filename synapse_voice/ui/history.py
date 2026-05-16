"""History viewer — last N transcriptions, click to copy/repaste."""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ..config import Config

DARK_QSS = """
QDialog { background: #020817; color: #e6f2fb; }
QLabel { color: #c8d6df; }
QListWidget {
    background: #0c1828; color: #e6f2fb; border: 1px solid #1f3145;
    border-radius: 6px; padding: 4px;
    selection-background-color: #103043;
    selection-color: #40d6ff;
}
QListWidget::item { padding: 8px; border-bottom: 1px solid #112233; }
QListWidget::item:hover { background: #0d1c2c; }
QPushButton {
    background: #0c1828; color: white; border: 1px solid #1f3145;
    border-radius: 6px; padding: 6px 14px; min-width: 110px;
}
QPushButton:hover { border-color: #40d6ff; }
QPushButton:default { background: #40d6ff; color: #020817; border: none; }
"""


class HistoryDialog(QDialog):
    def __init__(
        self,
        config: Config,
        on_repaste: Callable[[str], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sonar — History")
        self.setStyleSheet(DARK_QSS)
        self.resize(640, 480)
        self._config = config
        self._on_repaste = on_repaste

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        history_enabled = bool(getattr(config, "history_enabled", True))
        header_text = f"Last {len(config.history)} transcriptions"
        if not history_enabled:
            header_text += "  ·  Verlauf deaktiviert (Settings → Privatsphäre)"
        header = QLabel(header_text)
        header.setFont(QFont("Inter", 11, QFont.Weight.Medium))
        layout.addWidget(header)

        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setUniformItemSizes(False)
        self.list.setSpacing(4)
        self.list.itemDoubleClicked.connect(self._copy_selected)
        layout.addWidget(self.list, 1)

        self._rebuild_list()

        if not config.history:
            empty = QLabel("No transcriptions yet — your hotkey hasn't fired.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(empty, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(self._copy_selected)
        buttons.addWidget(copy_btn)

        repaste_btn = QPushButton("Re-paste")
        repaste_btn.clicked.connect(self._repaste_selected)
        buttons.addWidget(repaste_btn)

        # v0.9.11 (privacy): per-entry delete in addition to Clear-all,
        # so users can prune a single sensitive snippet without nuking
        # the whole log.
        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self._delete_selected)
        buttons.addWidget(delete_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear)
        buttons.addWidget(clear_btn)

        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)

        layout.addLayout(buttons)

    def _rebuild_list(self) -> None:
        from PyQt6.QtCore import QSize as _QSize

        self.list.clear()
        # We render newest-first but the underlying config.history is
        # oldest-first. Store the original index on each row so Delete
        # can pop the right entry.
        for original_idx, entry in [
            (i, e) for i, e in enumerate(self._config.history)
        ][::-1]:
            ts = entry.get("ts", "")
            mode = entry.get("mode", "?")
            paste_mode = entry.get("paste_mode", "")
            target = entry.get("target") or ""
            text = entry.get("text", "")
            tier = (entry.get("quality_mode") or "").strip().lower()
            preview = text if len(text) <= 240 else text[:240] + "…"
            tier_str = ""
            if tier and mode != "local":
                tier_emoji = {
                    "instant": "⚡", "fast": "⚡", "quality": "✦"
                }.get(tier, "·")
                tier_str = f"  ·  {tier_emoji} {tier}"
            label = (
                f"[{ts}]  {mode} → {paste_mode}{tier_str}"
                + (f"   ({target[:40]})" if target else "")
                + f"\n{preview}"
            )
            item = QListWidgetItem(label)
            # Stash both the text (for copy/repaste) and the original
            # index (for delete) on the row.
            item.setData(Qt.ItemDataRole.UserRole, text)
            item.setData(Qt.ItemDataRole.UserRole + 1, original_idx)
            line_count = max(2, 1 + (len(preview) // 70))
            item.setSizeHint(_QSize(0, 22 * line_count + 12))
            self.list.addItem(item)

    def _selected_text(self) -> str | None:
        item = self.list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _copy_selected(self) -> None:
        text = self._selected_text()
        if not text:
            return
        # Use the same native clipboard path the paste flow uses — Qt's clipboard
        # on Windows can lose ownership when the dialog closes, leaving an empty
        # clipboard. Native SetClipboardData transfers ownership properly.
        from ..target_lock import set_clipboard

        if not set_clipboard(text):
            QGuiApplication.clipboard().setText(text)  # last-resort fallback

    def _repaste_selected(self) -> None:
        text = self._selected_text()
        if text:
            self._on_repaste(text)
            self.accept()

    def _delete_selected(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        idx = item.data(Qt.ItemDataRole.UserRole + 1)
        if idx is None or not isinstance(idx, int):
            return
        try:
            del self._config.history[idx]
        except IndexError:
            return
        self._config.save()
        self._rebuild_list()

    def _clear(self) -> None:
        self._config.history = []
        self._config.save()
        self.list.clear()
