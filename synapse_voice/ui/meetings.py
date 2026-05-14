"""Meetings — long-form recording browser + Tasks/Decisions extractor.

Shows every long-form transcript Sonar has recorded (>= ``long_form_threshold_seconds``).
Lets the user flip between cleanup styles, extract action items / decisions, and
push them into the local Subunit Bridge.
"""
from __future__ import annotations

import json
import logging
from typing import Callable

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..bridge_client import BridgeClient, BridgeError
from ..cleanup_client import cleanup_text
from ..config import Config
from ..meetings import Meeting, MeetingsStore

_log = logging.getLogger(__name__)


# A subset of cleanup styles that make sense for the Meetings UI.
# (raw is always available as it's the persisted transcript itself.)
STYLE_LABELS: list[tuple[str, str]] = [
    ("raw", "Raw transcript"),
    ("summary", "Summary"),
    ("action_items", "Action items"),
    ("minutes", "Minutes"),
    ("decisions", "Decisions"),
    ("tidy", "Tidy"),
]


DARK_QSS = """
QDialog { background: #020817; color: #e6f2fb; }
QLabel { color: #c8d6df; }
QLabel#h1 { font-size: 18px; font-weight: 600; color: #e6f2fb; }
QLabel#dim { color: #5f7689; font-size: 11px; }
QLabel#chip {
    background: #0c1828; color: #40d6ff; padding: 3px 10px;
    border: 1px solid #1f3145; border-radius: 999px; font-size: 11px;
}
QLineEdit {
    background: #0c1828; color: #e6f2fb; border: 1px solid #1f3145;
    border-radius: 6px; padding: 7px 12px;
}
QLineEdit:focus { border-color: #40d6ff; }
QListWidget {
    background: #0c1828; color: #e6f2fb; border: 1px solid #1f3145;
    border-radius: 6px; padding: 4px;
    selection-background-color: #103043; selection-color: #40d6ff;
}
QListWidget::item { padding: 8px 10px; border-bottom: 1px solid #112233; }
QListWidget::item:hover { background: #0d1c2c; }
QTextEdit {
    background: #0c1828; color: #e6f2fb; border: 1px solid #1f3145;
    border-radius: 6px; padding: 10px; font-family: 'Inter', 'Segoe UI', sans-serif;
}
QComboBox {
    background: #0c1828; color: #e6f2fb; border: 1px solid #1f3145;
    border-radius: 6px; padding: 6px 10px;
}
QComboBox:hover { border-color: #40d6ff; }
QComboBox QAbstractItemView {
    background: #0c1828; color: #e6f2fb;
    selection-background-color: #103043; selection-color: #40d6ff;
    border: 1px solid #1f3145;
}
QPushButton {
    background: #0c1828; color: white; border: 1px solid #1f3145;
    border-radius: 6px; padding: 7px 14px;
}
QPushButton:hover { border-color: #40d6ff; }
QPushButton:disabled { color: #5f7689; }
QPushButton#primary { background: #40d6ff; color: #020817; border: none; font-weight: 600; }
QPushButton#primary:hover { background: #5be2ff; }
QPushButton#danger { color: #ff7676; border-color: #4a1f1f; }
QPushButton#danger:hover { border-color: #ff7676; }
QCheckBox { color: #c8d6df; spacing: 8px; }
QCheckBox::indicator {
    width: 14px; height: 14px; border: 1px solid #345066; border-radius: 3px;
    background: #0c1828;
}
QCheckBox::indicator:checked { background: #40d6ff; border-color: #40d6ff; }
"""


# ----------------------------------------------------------------------
# Background worker for cleanup-API calls (so the UI thread stays free).
# ----------------------------------------------------------------------
class CleanupWorker(QThread):
    """Runs ``cleanup_text`` off the UI thread.

    Emits ``done(style, text)`` on success or ``failed(style, error_msg)``.
    """

    done = pyqtSignal(str, str)
    failed = pyqtSignal(str, str)

    def __init__(self, text: str, style: str, endpoint: str, api_key: str | None) -> None:
        super().__init__()
        self._text = text
        self._style = style
        self._endpoint = endpoint
        self._api_key = api_key

    def run(self) -> None:
        try:
            result = cleanup_text(
                self._text,
                transcribe_endpoint=self._endpoint,
                api_key=self._api_key,
                style=self._style,
            )
            self.done.emit(self._style, result or "")
        except Exception as e:
            self.failed.emit(self._style, str(e))


# ----------------------------------------------------------------------
# Tasks/Decisions extraction dialog
# ----------------------------------------------------------------------
class ExtractDialog(QDialog):
    """Confirm which extracted items to push into the Bridge."""

    def __init__(
        self,
        kind: str,  # "task" or "decision"
        items: list[str],
        bridge: BridgeClient,
        meeting: Meeting,
        on_done: Callable[[int], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Extracted {kind.title()}s — confirm")
        self.setStyleSheet(DARK_QSS)
        self.resize(520, 480)
        self._kind = kind
        self._bridge = bridge
        self._meeting = meeting
        self._on_done = on_done
        self._checks: list[tuple[QCheckBox, str]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        head = QLabel(f"{len(items)} {kind}s detected — pick which to push to your Subunit Inbox.")
        head.setObjectName("h1")
        layout.addWidget(head)

        from PyQt6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body_l = QVBoxLayout(body)
        body_l.setSpacing(8)
        body_l.setContentsMargins(2, 2, 2, 2)
        for item in items:
            cb = QCheckBox(item)
            cb.setChecked(True)
            cb.setWordWrap(True)
            body_l.addWidget(cb)
            self._checks.append((cb, item))
        body_l.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        push = QPushButton(f"Push to Bridge")
        push.setObjectName("primary")
        push.clicked.connect(self._on_push_clicked)
        btns.addWidget(cancel)
        btns.addWidget(push)
        layout.addLayout(btns)

    def _on_push_clicked(self) -> None:
        selected = [text for cb, text in self._checks if cb.isChecked()]
        if not selected:
            self.reject()
            return
        pushed = 0
        errors: list[str] = []
        for item in selected:
            try:
                if self._kind == "task":
                    self._bridge.create_task(
                        item,
                        metadata={
                            "source_meeting_id": self._meeting.id,
                            "meeting_title": self._meeting.title,
                        },
                    )
                else:
                    self._bridge.create_decision(
                        item,
                        body=f"Extracted from meeting {self._meeting.title} ({self._meeting.created_at_local_str})",
                        source="sonar",
                        metadata={
                            "source_meeting_id": self._meeting.id,
                            "meeting_title": self._meeting.title,
                        },
                    )
                pushed += 1
            except BridgeError as e:
                errors.append(str(e))
        if errors:
            QMessageBox.warning(
                self,
                "Bridge",
                f"Pushed {pushed}/{len(selected)}. Errors:\n" + "\n".join(errors[:3]),
            )
        self._on_done(pushed)
        self.accept()


# ----------------------------------------------------------------------
# Main dialog
# ----------------------------------------------------------------------
class MeetingsDialog(QDialog):
    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sonar — Meetings")
        self.setStyleSheet(DARK_QSS)
        self.resize(960, 620)
        self._config = config
        self._store = MeetingsStore()
        self._bridge = BridgeClient()
        self._current: Meeting | None = None
        self._current_style: str = "raw"
        self._cleanup_worker: CleanupWorker | None = None
        self._build_ui()
        self._refresh_list()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(10)

        head_row = QHBoxLayout()
        head = QLabel("Meetings")
        head.setObjectName("h1")
        head_row.addWidget(head)
        head_row.addStretch()
        self._bridge_status = QLabel("")
        self._bridge_status.setObjectName("dim")
        head_row.addWidget(self._bridge_status)
        outer.addLayout(head_row)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search transcripts…")
        self.search.textChanged.connect(self._refresh_list)
        outer.addWidget(self.search)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(2)

        # Left: meetings list
        self.list = QListWidget()
        self.list.setUniformItemSizes(False)
        self.list.itemSelectionChanged.connect(self._on_selection_changed)
        self.list.setMinimumWidth(260)
        splitter.addWidget(self.list)

        # Right: detail panel
        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(8, 0, 0, 0)
        right_l.setSpacing(8)

        self.detail_title = QLabel("Select a meeting on the left")
        self.detail_title.setObjectName("h1")
        self.detail_title.setWordWrap(True)
        right_l.addWidget(self.detail_title)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)
        self.chip_date = QLabel("")
        self.chip_date.setObjectName("chip")
        self.chip_duration = QLabel("")
        self.chip_duration.setObjectName("chip")
        self.chip_source = QLabel("")
        self.chip_source.setObjectName("chip")
        self.chip_lang = QLabel("")
        self.chip_lang.setObjectName("chip")
        for chip in (self.chip_date, self.chip_duration, self.chip_source, self.chip_lang):
            chip.hide()
            meta_row.addWidget(chip)
        meta_row.addStretch()
        right_l.addLayout(meta_row)

        style_row = QHBoxLayout()
        style_row.setSpacing(8)
        style_lbl = QLabel("View")
        style_lbl.setObjectName("dim")
        style_row.addWidget(style_lbl)
        self.style_picker = QComboBox()
        for style_id, label in STYLE_LABELS:
            self.style_picker.addItem(label, style_id)
        self.style_picker.currentIndexChanged.connect(self._on_style_changed)
        style_row.addWidget(self.style_picker)
        self.regen_btn = QPushButton("↻ Regenerate")
        self.regen_btn.setToolTip("Force a fresh AI cleanup for this style.")
        self.regen_btn.clicked.connect(self._on_regen_clicked)
        style_row.addWidget(self.regen_btn)
        style_row.addStretch()
        right_l.addLayout(style_row)

        self.body = QTextEdit()
        self.body.setReadOnly(True)
        self.body.setFont(QFont("Inter", 10))
        right_l.addWidget(self.body, 1)

        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)
        self.btn_tasks = QPushButton("Extract Tasks → Bridge")
        self.btn_tasks.setObjectName("primary")
        self.btn_tasks.clicked.connect(lambda: self._extract_to_bridge(kind="task"))
        self.btn_decisions = QPushButton("Extract Decisions → Bridge")
        self.btn_decisions.clicked.connect(lambda: self._extract_to_bridge(kind="decision"))
        self.btn_copy = QPushButton("Copy")
        self.btn_copy.clicked.connect(self._on_copy_clicked)
        self.btn_delete = QPushButton("Delete")
        self.btn_delete.setObjectName("danger")
        self.btn_delete.clicked.connect(self._on_delete_clicked)
        for b in (self.btn_tasks, self.btn_decisions, self.btn_copy, self.btn_delete):
            b.setEnabled(False)
            actions_row.addWidget(b)
        actions_row.addStretch()
        right_l.addLayout(actions_row)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 620])
        outer.addWidget(splitter, 1)

        # Refresh bridge status on open + every ~5s while dialog is open.
        self._bridge_timer = QTimer(self)
        self._bridge_timer.setInterval(5000)
        self._bridge_timer.timeout.connect(self._refresh_bridge_status)
        self._bridge_timer.start()
        QTimer.singleShot(0, self._refresh_bridge_status)

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------
    def _refresh_list(self) -> None:
        query = self.search.text().strip()
        meetings = self._store.search(query) if query else self._store.list_all()
        self.list.clear()
        if not meetings:
            placeholder = QListWidgetItem("No meetings yet — long-form recordings (>=240s) show up here.")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(placeholder)
            return
        for m in meetings:
            item = QListWidgetItem()
            item.setText(self._render_list_text(m))
            item.setData(Qt.ItemDataRole.UserRole, m.id)
            self.list.addItem(item)

    def _render_list_text(self, m: Meeting) -> str:
        title = m.title
        if len(title) > 64:
            title = title[:61] + "…"
        return f"{title}\n{m.created_at_local_str}  ·  {m.duration_str}  ·  {m.source}"

    # ------------------------------------------------------------------
    # Selection / detail
    # ------------------------------------------------------------------
    def _on_selection_changed(self) -> None:
        items = self.list.selectedItems()
        if not items:
            return
        meeting_id = items[0].data(Qt.ItemDataRole.UserRole)
        if not meeting_id:
            return
        self._current = self._store.get(meeting_id)
        if not self._current:
            return
        self._show_meeting(self._current)

    def _show_meeting(self, m: Meeting) -> None:
        self.detail_title.setText(m.title)
        self.chip_date.setText(m.created_at_local_str)
        self.chip_duration.setText(m.duration_str)
        self.chip_source.setText(m.source or "Microphone")
        self.chip_lang.setText((m.language or "auto").upper())
        for chip in (self.chip_date, self.chip_duration, self.chip_source, self.chip_lang):
            chip.show()
        for b in (self.btn_tasks, self.btn_decisions, self.btn_copy, self.btn_delete):
            b.setEnabled(True)
        # Pick the currently-shown style — default to "raw" so the user always
        # sees the actual transcript first.
        idx = self.style_picker.findData("raw")
        if idx >= 0:
            self.style_picker.blockSignals(True)
            self.style_picker.setCurrentIndex(idx)
            self.style_picker.blockSignals(False)
        self._current_style = "raw"
        self._render_body()

    def _render_body(self, *, force_regen: bool = False) -> None:
        if not self._current:
            return
        style = self._current_style
        if style == "raw" or style == "prompt":
            self.body.setPlainText(self._current.transcript_raw or "(empty)")
            return
        cached = (self._current.cleanup_versions or {}).get(style)
        if cached and not force_regen:
            self.body.setPlainText(cached)
            return
        self.body.setPlainText(f"⏳ Generating {style} via AI…")
        # Run cleanup off the UI thread
        worker = CleanupWorker(
            text=self._current.transcript_raw,
            style=style,
            endpoint=self._config.subunit_endpoint,
            api_key=self._config.subunit_api_key,
        )
        worker.done.connect(self._on_cleanup_done)
        worker.failed.connect(self._on_cleanup_failed)
        # Hold a reference so the QThread isn't garbage-collected mid-run.
        self._cleanup_worker = worker
        worker.start()

    def _on_cleanup_done(self, style: str, text: str) -> None:
        if not self._current or style != self._current_style:
            return
        self._current.cleanup_versions[style] = text
        self._store.update(self._current)
        self.body.setPlainText(text or "(empty result)")

    def _on_cleanup_failed(self, style: str, error: str) -> None:
        if not self._current or style != self._current_style:
            return
        self.body.setPlainText(
            f"AI cleanup failed for style '{style}':\n{error}\n\nShowing raw transcript:\n\n"
            + (self._current.transcript_raw or "")
        )

    def _on_style_changed(self, _idx: int) -> None:
        style = self.style_picker.currentData()
        if not style:
            return
        self._current_style = style
        self._render_body()

    def _on_regen_clicked(self) -> None:
        self._render_body(force_regen=True)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _on_copy_clicked(self) -> None:
        from PyQt6.QtGui import QGuiApplication
        text = self.body.toPlainText()
        if text:
            QGuiApplication.clipboard().setText(text)

    def _on_delete_clicked(self) -> None:
        if not self._current:
            return
        confirm = QMessageBox.question(
            self,
            "Delete meeting?",
            f"Delete '{self._current.title}'?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._store.delete(self._current.id)
        self._current = None
        self.detail_title.setText("Select a meeting on the left")
        for chip in (self.chip_date, self.chip_duration, self.chip_source, self.chip_lang):
            chip.hide()
        self.body.clear()
        for b in (self.btn_tasks, self.btn_decisions, self.btn_copy, self.btn_delete):
            b.setEnabled(False)
        self._refresh_list()

    def _extract_to_bridge(self, *, kind: str) -> None:
        if not self._current:
            return
        if not self._bridge.is_available():
            QMessageBox.warning(
                self,
                "Bridge not running",
                "The local Subunit Bridge daemon is not reachable on localhost:7842.\n\n"
                "Install + start `subunit-bridge` to push items to your Subunit Inbox.",
            )
            return
        if not self._bridge.is_paired():
            QMessageBox.warning(
                self,
                "Bridge not paired",
                "The Subunit Bridge is running but not paired with an account.\n\n"
                "Open the Subunit CLI (`subunit pair`) or pair via the Bridge UI first.",
            )
            return
        # Run the extraction on the background thread.
        style = "action_items" if kind == "task" else "decisions"
        self.body.setPlainText(f"⏳ Asking AI for {style}…")
        worker = CleanupWorker(
            text=self._current.transcript_raw,
            style=style,
            endpoint=self._config.subunit_endpoint,
            api_key=self._config.subunit_api_key,
        )
        worker.done.connect(lambda s, t, k=kind: self._on_extract_ready(k, s, t))
        worker.failed.connect(lambda s, e: self._on_cleanup_failed(s, e))
        self._cleanup_worker = worker
        worker.start()

    def _on_extract_ready(self, kind: str, style: str, text: str) -> None:
        if not self._current:
            return
        # Cache the cleanup result for the user too.
        self._current.cleanup_versions[style] = text
        self._store.update(self._current)
        # Pick the style in the UI so the user sees what we extracted from.
        idx = self.style_picker.findData(style)
        if idx >= 0:
            self.style_picker.blockSignals(True)
            self.style_picker.setCurrentIndex(idx)
            self.style_picker.blockSignals(False)
            self._current_style = style
            self.body.setPlainText(text)
        items = _parse_list_items(text)
        if not items:
            QMessageBox.information(
                self,
                f"No {kind}s found",
                f"The AI didn't return a list of {kind}s. You can copy the body manually.",
            )
            return
        dlg = ExtractDialog(
            kind=kind,
            items=items,
            bridge=self._bridge,
            meeting=self._current,
            on_done=self._on_extract_pushed,
            parent=self,
        )
        dlg.exec()

    def _on_extract_pushed(self, count: int) -> None:
        if not self._current:
            return
        # Update meeting counters.
        if count > 0:
            # We don't know which kind from this callback alone, so bump both
            # extracted_* in a way that the caller picked — but for simplicity
            # we just bump the total here.
            self._current.metadata["last_extract_count"] = count
            self._store.update(self._current)

    # ------------------------------------------------------------------
    # Bridge availability indicator
    # ------------------------------------------------------------------
    def _refresh_bridge_status(self) -> None:
        st = self._bridge.status()
        if not st or not st.get("paired"):
            if self._bridge.is_available():
                self._bridge_status.setText("Bridge: available, not paired")
            else:
                self._bridge_status.setText("Bridge: not running")
        else:
            email = st.get("email") or ""
            self._bridge_status.setText(f"Bridge: paired as {email}")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _parse_list_items(text: str) -> list[str]:
    """Extract a list of items from an AI-cleanup result.

    Accepts JSON arrays of strings, Markdown bullet lists (``- foo``, ``* foo``,
    ``1. foo``), and falls back to non-empty lines.
    """
    if not text:
        return []
    stripped = text.strip()
    # JSON array?
    if stripped.startswith("[") and stripped.endswith("]"):
        try:
            data = json.loads(stripped)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except Exception:
            pass
    items: list[str] = []
    for line in stripped.splitlines():
        s = line.strip()
        if not s:
            continue
        # Skip section headings
        if s.startswith("#"):
            continue
        # Bullet markers
        for marker in ("- ", "* ", "• "):
            if s.startswith(marker):
                s = s[len(marker):].strip()
                break
        # Numbered list "1. ", "12) "
        import re
        s = re.sub(r"^\d+[.)]\s+", "", s)
        if s:
            items.append(s)
    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for it in items:
        key = it.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(it)
    return result
