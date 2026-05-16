"""Meeting Host Modal — show QR-code, share-link, live check-in list.

Opens when the user clicks "Meeting starten" in Sonar. The dialog:
  1. Asks for a meeting title (one-liner)
  2. POSTs to /v1/meetings with host_name (config.account_email) + title
  3. Renders the QR-code + 6-digit code + share-link in a two-column layout
  4. Polls /v1/meetings/<code>/participants every 2s and updates a list
  5. Has "Aufnahme starten" / "Meeting beenden" buttons that POST to /start|end

WebRTC audio capture comes in Phase 2; for now the dialog acts as the
session manager and Sonar's existing local recorder remains the audio
source. Diarization (v0.8.0) tags the host's audio; participant audio
joins in Phase 2 when the SFU lands.
"""
from __future__ import annotations

import io
import logging
from typing import Callable

from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QGuiApplication, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from ..meet_client import (
    Meeting,
    Participant,
    approve_participant,
    create_meeting,
    end_meeting,
    host_self_join,
    list_participants,
    reject_participant,
    start_meeting,
)
from ..meet_host_stream import HostStreamer

_log = logging.getLogger(__name__)


CYAN = "#22d3ee"
CYAN_GLOW = "#40d6ff"
NIGHT = "#020817"
NIGHT_2 = "#0c1828"
NIGHT_BORDER = "#1f3145"
WHITE = "#e6f2fb"
WHITE_DIM = "#9fb1bd"
AMBER = "#fbbf24"
EMERALD = "#10b981"
RED = "#ff6b6b"


DARK_QSS = f"""
QDialog {{ background: {NIGHT}; color: {WHITE}; }}
QLabel {{ color: {WHITE}; }}
QLabel#h1 {{ font-size: 20px; font-weight: 700; letter-spacing: -0.01em; }}
QLabel#eyebrow {{
    color: {CYAN_GLOW}; font-size: 11px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 1.5px;
}}
QLabel#dim {{ color: {WHITE_DIM}; font-size: 12px; }}
QLabel#code-display {{
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 36px; font-weight: 800; letter-spacing: 8px;
    color: {WHITE}; padding: 14px;
    background: {NIGHT_2}; border: 1px solid {NIGHT_BORDER};
    border-radius: 12px;
}}
QLabel#share-link {{
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 12px; color: {CYAN_GLOW};
    padding: 8px 10px; background: {NIGHT_2};
    border: 1px solid {NIGHT_BORDER}; border-radius: 8px;
}}
QLineEdit {{
    background: {NIGHT_2}; color: {WHITE};
    border: 1px solid {NIGHT_BORDER}; border-radius: 8px;
    padding: 9px 12px; font-size: 14px;
}}
QLineEdit:focus {{ border-color: {CYAN_GLOW}; }}
QListWidget {{
    background: {NIGHT_2}; color: {WHITE};
    border: 1px solid {NIGHT_BORDER}; border-radius: 10px;
    padding: 6px;
}}
QListWidget::item {{
    padding: 8px 10px; border-bottom: 1px solid #112233;
}}
QListWidget::item:last-child {{ border-bottom: none; }}
QPushButton {{
    background: {NIGHT_2}; color: {WHITE};
    border: 1px solid {NIGHT_BORDER}; border-radius: 8px;
    padding: 9px 16px; font-weight: 500;
}}
QPushButton:hover {{ border-color: {CYAN_GLOW}; }}
QPushButton:disabled {{ color: {WHITE_DIM}; }}
QPushButton#primary {{
    background: {CYAN_GLOW}; color: {NIGHT}; border: none; font-weight: 700;
}}
QPushButton#primary:hover {{ background: {CYAN}; }}
QPushButton#danger {{ color: {RED}; border-color: #4a1f1f; }}
QPushButton#danger:hover {{ border-color: {RED}; }}
QPushButton#rec-active {{
    background: {RED}; color: white; border: none;
    font-weight: 700;
}}
QFrame#qr-frame {{
    background: white; border-radius: 12px; padding: 10px;
}}
"""


def _render_qr_pixmap(text: str, *, size: int = 220) -> QPixmap | None:
    """Render a QR-code for ``text`` into a QPixmap.

    Falls back to ``None`` if the qrcode/pillow stack isn't available so
    the dialog can still show the code + share-link without the visual.
    """
    try:
        import qrcode
        from PIL import Image  # noqa: F401  (qrcode imports pillow under the hood)
    except ImportError as e:
        _log.warning("qrcode lib not available: %s", e)
        return None
    try:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
        )
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        pix = QPixmap()
        pix.loadFromData(buf.getvalue(), "PNG")
        return pix.scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    except Exception as e:
        _log.warning("QR render failed: %s", e)
        return None


class MeetingHostDialog(QDialog):
    """The host's window for an active meeting session."""

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sonar — Meeting starten")
        self.setStyleSheet(DARK_QSS)
        self.resize(720, 520)
        self._config = config
        self._meeting: Meeting | None = None
        self._poll_timer: QTimer | None = None
        self._host_join_token: str | None = None
        self._host_streamer: HostStreamer | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(14)

        # Step 1: title entry (shown until POST succeeds)
        self.step_create = QWidget()
        c_layout = QVBoxLayout(self.step_create)
        c_layout.setSpacing(10)
        c_eyebrow = QLabel("MEETING STARTEN")
        c_eyebrow.setObjectName("eyebrow")
        c_layout.addWidget(c_eyebrow)
        c_title = QLabel("Wie heisst dieses Meeting?")
        c_title.setObjectName("h1")
        c_layout.addWidget(c_title)
        c_hint = QLabel(
            "Wird den Teilnehmern in der Einladung angezeigt. Optional — "
            "wenn leer, generieren wir 'Meeting #<code>'."
        )
        c_hint.setObjectName("dim")
        c_hint.setWordWrap(True)
        c_layout.addWidget(c_hint)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("z. B. Q3 Pricing Review mit Marko")
        c_layout.addWidget(self.title_edit)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Abbrechen")
        cancel.clicked.connect(self.reject)
        self.btn_create = QPushButton("Code generieren")
        self.btn_create.setObjectName("primary")
        self.btn_create.clicked.connect(self._on_create_clicked)
        btn_row.addWidget(cancel)
        btn_row.addWidget(self.btn_create)
        c_layout.addLayout(btn_row)
        c_layout.addStretch()
        outer.addWidget(self.step_create)

        # Step 2: meeting active (shown after create)
        self.step_active = QWidget()
        self.step_active.hide()
        a_layout = QHBoxLayout(self.step_active)
        a_layout.setSpacing(20)

        # Left column: QR + code + share-link
        left = QVBoxLayout()
        left.setSpacing(12)
        a_eyebrow = QLabel("MEETING LÄUFT — TEILNEHMER CHECKEN EIN")
        a_eyebrow.setObjectName("eyebrow")
        left.addWidget(a_eyebrow)
        self.meeting_title_lbl = QLabel("…")
        self.meeting_title_lbl.setObjectName("h1")
        self.meeting_title_lbl.setWordWrap(True)
        left.addWidget(self.meeting_title_lbl)

        qr_frame = QFrame()
        qr_frame.setObjectName("qr-frame")
        qr_frame.setFixedSize(240, 240)
        qr_layout = QVBoxLayout(qr_frame)
        qr_layout.setContentsMargins(10, 10, 10, 10)
        self.qr_label = QLabel("…")
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_layout.addWidget(self.qr_label)
        left.addWidget(qr_frame, alignment=Qt.AlignmentFlag.AlignLeft)

        self.code_lbl = QLabel("000 000")
        self.code_lbl.setObjectName("code-display")
        self.code_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left.addWidget(self.code_lbl)

        self.share_link_lbl = QLabel("https://meet.subunit.ai/…")
        self.share_link_lbl.setObjectName("share-link")
        self.share_link_lbl.setWordWrap(True)
        left.addWidget(self.share_link_lbl)

        copy_row = QHBoxLayout()
        self.btn_copy_code = QPushButton("Code kopieren")
        self.btn_copy_code.clicked.connect(self._on_copy_code)
        self.btn_copy_link = QPushButton("Link kopieren")
        self.btn_copy_link.clicked.connect(self._on_copy_link)
        copy_row.addWidget(self.btn_copy_code)
        copy_row.addWidget(self.btn_copy_link)
        copy_row.addStretch()
        left.addLayout(copy_row)
        left.addStretch()
        a_layout.addLayout(left, 1)

        # Right column: participants list + start/end buttons
        right = QVBoxLayout()
        right.setSpacing(10)
        r_eyebrow = QLabel("EINGECHECKT")
        r_eyebrow.setObjectName("eyebrow")
        right.addWidget(r_eyebrow)
        self.participants_list = QListWidget()
        self.participants_list.setUniformItemSizes(False)
        right.addWidget(self.participants_list, 1)

        self.participant_count_lbl = QLabel("0 Teilnehmer warten")
        self.participant_count_lbl.setObjectName("dim")
        right.addWidget(self.participant_count_lbl)

        right.addSpacing(8)
        self.btn_start = QPushButton("🔴 Aufnahme starten")
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self._on_start_clicked)
        right.addWidget(self.btn_start)

        self.btn_end = QPushButton("Meeting beenden")
        self.btn_end.setObjectName("danger")
        self.btn_end.clicked.connect(self._on_end_clicked)
        right.addWidget(self.btn_end)

        end_hint = QLabel(
            "Tipp: Du kannst auch ohne Wartezeit starten. Spätere Teilnehmer "
            "können trotzdem noch einchecken — werden zur laufenden Aufnahme "
            "hinzugefügt."
        )
        end_hint.setObjectName("dim")
        end_hint.setWordWrap(True)
        right.addWidget(end_hint)

        a_layout.addLayout(right, 1)
        outer.addWidget(self.step_active)

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------
    def _on_create_clicked(self) -> None:
        title = self.title_edit.text().strip()
        host_name = (self._config.account_email or "").split("@")[0] or "Host"
        self.btn_create.setEnabled(False)
        self.btn_create.setText("Erstelle…")
        # Run create off the UI thread so we don't freeze on slow networks.
        worker = _CreateWorker(self._config, host_name, title)
        worker.done.connect(self._on_create_done)
        worker.failed.connect(self._on_create_failed)
        self._create_worker = worker  # keep reference
        worker.start()

    def _on_create_done(self, meeting: Meeting) -> None:
        self._meeting = meeting
        self.step_create.hide()
        self.step_active.show()
        self.meeting_title_lbl.setText(meeting.title)
        self.code_lbl.setText(f"{meeting.code[:3]} {meeting.code[3:]}")
        self.share_link_lbl.setText(meeting.share_url)
        pix = _render_qr_pixmap(meeting.share_url)
        if pix:
            self.qr_label.setPixmap(pix)
        else:
            self.qr_label.setText("(QR-Render fehlgeschlagen — Code/Link reichen)")
        # Self-register the host as a participant so their own mic flows
        # through the same /audio/{token} WS as the guests'. The post-
        # pipeline labels their segments with `host_name` — no special-
        # casing needed downstream.
        host_name = (self._config.account_email or "").split("@")[0] or "Host"
        self._host_join_token = host_self_join(
            self._config.subunit_endpoint,
            meeting.code,
            host_name=host_name,
            host_email=self._config.account_email or None,
        )
        if not self._host_join_token:
            _log.warning("[meet] host self-join failed — host mic won't be in recap")
        # Start polling participants every 2 seconds.
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(2000)
        self._poll_timer.timeout.connect(self._poll_participants)
        self._poll_timer.start()
        self._poll_participants()  # immediate first poll

    def _on_create_failed(self, msg: str) -> None:
        self.btn_create.setEnabled(True)
        self.btn_create.setText("Code generieren")
        QMessageBox.warning(
            self,
            "Meeting konnte nicht erstellt werden",
            f"{msg}\n\nPrüfe deine Subunit-Anmeldung in Settings → Account.",
        )

    # ------------------------------------------------------------------
    # Polling + actions
    # ------------------------------------------------------------------
    def _poll_participants(self) -> None:
        if not self._meeting:
            return
        worker = _PollWorker(self._config, self._meeting)
        worker.done.connect(self._on_poll_done)
        self._poll_worker = worker
        worker.start()

    def _on_poll_done(self, items: list[Participant]) -> None:
        self.participants_list.clear()
        # Sort pending guests to the top so the host can act fast.
        items_sorted = sorted(items, key=lambda p: (not p.pending, p.joined_at_relative))
        for p in items_sorted:
            it = QListWidgetItem()
            if p.pending:
                # Pending guests: embed ✓ / ✗ buttons via setItemWidget.
                row = self._build_pending_row(p)
                it.setSizeHint(row.sizeHint())
                self.participants_list.addItem(it)
                self.participants_list.setItemWidget(it, row)
            else:
                it.setText(self._render_participant(p))
                self.participants_list.addItem(it)
        n = len(items)
        pending = sum(1 for p in items if p.pending)
        if n == 0:
            self.participant_count_lbl.setText("0 Teilnehmer — warte auf Beitritte…")
        elif pending > 0:
            self.participant_count_lbl.setText(
                f"{n} Teilnehmer · {pending} wartet auf Freigabe"
            )
        else:
            self.participant_count_lbl.setText(f"{n} Teilnehmer eingecheckt")

    def _render_participant(self, p: Participant) -> str:
        # Render as "✓ Name           via QR · vor 12 Sek"
        source_label = {
            "qr": "via QR",
            "code": "Code-tipp",
            "host": "Host",
            "web": "Web",
        }.get(p.source, p.source)
        return f"✓ {p.name}    ·    {source_label}    ·    {p.joined_at_relative}"

    def _build_pending_row(self, p: Participant) -> QWidget:
        """A clickable row: name + ✓ Zulassen + ✗ Rauswerfen buttons."""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(8, 4, 8, 4)
        h.setSpacing(8)
        source_label = {"qr": "via QR", "code": "Code-tipp", "web": "Web"}.get(p.source, p.source)
        lbl = QLabel(f"⏳ <b>{p.name}</b>    ·    {source_label}    ·    {p.joined_at_relative}")
        lbl.setStyleSheet(f"color: {AMBER}; font-size: 13px;")
        h.addWidget(lbl, 1)
        btn_ok = QPushButton("✓ Zulassen")
        btn_ok.setStyleSheet(
            f"QPushButton {{ background: {EMERALD}; color: {NIGHT}; font-weight: 700;"
            f"  border: none; border-radius: 6px; padding: 6px 12px; font-size: 12px; }}"
            f"QPushButton:hover {{ background: #34d399; }}"
        )
        btn_ok.clicked.connect(lambda _=False, t=p.token: self._on_approve_clicked(t))
        h.addWidget(btn_ok)
        btn_no = QPushButton("✗")
        btn_no.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {RED}; font-weight: 700;"
            f"  border: 1px solid {RED}; border-radius: 6px; padding: 6px 10px; font-size: 12px; }}"
            f"QPushButton:hover {{ background: rgba(255,107,107,0.12); }}"
        )
        btn_no.setToolTip("Rauswerfen")
        btn_no.clicked.connect(lambda _=False, t=p.token: self._on_reject_clicked(t))
        h.addWidget(btn_no)
        return row

    def _on_approve_clicked(self, participant_token: str) -> None:
        if not self._meeting or not participant_token:
            return
        ok = approve_participant(
            self._config.subunit_endpoint,
            self._meeting.code,
            participant_token,
            self._meeting.host_token,
        )
        if not ok:
            _log.warning("[meet] approve failed for token %s…", participant_token[:8])
        # Re-poll right away so the row updates without the 2s wait.
        self._poll_participants()

    def _on_reject_clicked(self, participant_token: str) -> None:
        if not self._meeting or not participant_token:
            return
        confirm = QMessageBox.question(
            self,
            "Teilnehmer rauswerfen?",
            "Der Teilnehmer kann nicht mehr beitreten und sieht eine Ablehnungs-Nachricht. "
            "Weiter?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        reject_participant(
            self._config.subunit_endpoint,
            self._meeting.code,
            participant_token,
            self._meeting.host_token,
        )
        self._poll_participants()

    def _on_start_clicked(self) -> None:
        if not self._meeting:
            return
        if not start_meeting(
            self._config.subunit_endpoint,
            self._meeting.code,
            self._meeting.host_token,
        ):
            QMessageBox.warning(self, "Fehler", "Aufnahme konnte nicht gestartet werden.")
            return

        # Begin streaming the host's local mic to the same /audio/{token}
        # WebSocket the guests use. Failures are non-fatal — the guests'
        # streams alone are still enough to produce a recap.
        if self._host_join_token:
            try:
                # Resolve mic device the same way main.py does — by saved name.
                device = None
                mic_name = getattr(self._config, "mic_device_name", "") or ""
                if mic_name:
                    import sounddevice as _sd
                    for idx, dev in enumerate(_sd.query_devices()):
                        if int(dev.get("max_input_channels", 0)) > 0 and dev.get("name") == mic_name:
                            device = idx
                            break
                self._host_streamer = HostStreamer(
                    endpoint=self._config.subunit_endpoint,
                    code=self._meeting.code,
                    join_token=self._host_join_token,
                    device=device,
                )
                self._host_streamer.start()
            except Exception as e:  # noqa: BLE001
                _log.warning("[meet] host streamer failed to start: %s", e)
                self._host_streamer = None
                QMessageBox.information(
                    self,
                    "Host-Mic nicht im Recap",
                    "Aufnahme läuft, aber dein Mic ist nicht im automatischen "
                    "Protokoll. Die Gäste-Streams werden trotzdem verarbeitet.\n\n"
                    f"Grund: {e}",
                )

        self.btn_start.setObjectName("rec-active")
        self.btn_start.setText("🔴 Aufnahme läuft")
        self.btn_start.setEnabled(False)
        self.btn_start.setStyleSheet(self.btn_start.styleSheet())  # force re-apply
        # Re-apply stylesheet so the objectName change paints.
        self.style().unpolish(self.btn_start)
        self.style().polish(self.btn_start)

    def _on_end_clicked(self) -> None:
        if not self._meeting:
            self.reject()
            return
        confirm = QMessageBox.question(
            self,
            "Meeting beenden?",
            "Das beendet die Aufnahme und schickt jedem Teilnehmer sein "
            "persönliches Protokoll per E-Mail.\n\nWeiter?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        # Stop the host mic stream FIRST so ffmpeg flushes the webm
        # container before /end fires the post-meeting pipeline.
        if self._host_streamer is not None:
            try:
                self._host_streamer.stop(timeout=6.0)
            except Exception as e:  # noqa: BLE001
                _log.warning("[meet] host streamer stop error: %s", e)
            self._host_streamer = None
        end_meeting(
            self._config.subunit_endpoint,
            self._meeting.code,
            self._meeting.host_token,
        )
        if self._poll_timer:
            self._poll_timer.stop()
        self.accept()

    def _on_copy_code(self) -> None:
        if not self._meeting:
            return
        QGuiApplication.clipboard().setText(self._meeting.code)
        self.btn_copy_code.setText("Kopiert ✓")
        QTimer.singleShot(1500, lambda: self.btn_copy_code.setText("Code kopieren"))

    def _on_copy_link(self) -> None:
        if not self._meeting:
            return
        QGuiApplication.clipboard().setText(self._meeting.share_url)
        self.btn_copy_link.setText("Kopiert ✓")
        QTimer.singleShot(1500, lambda: self.btn_copy_link.setText("Link kopieren"))

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        # Codex review v0.9.2 #3: stop the streamer + end the meeting
        # before tearing the dialog down. Without this, closing the
        # window mid-recording would leave the mic open and the WS
        # uploading audio that never makes it into a recap.
        if self._host_streamer is not None:
            try:
                self._host_streamer.stop(timeout=4.0)
            except Exception as e:  # noqa: BLE001
                _log.warning("[meet] closeEvent: streamer stop error: %s", e)
            self._host_streamer = None
        if self._meeting and self._meeting.host_token:
            # Best-effort — server-side WS handler also checks status,
            # so even if this fails the pipeline won't poison the recap.
            try:
                end_meeting(
                    self._config.subunit_endpoint,
                    self._meeting.code,
                    self._meeting.host_token,
                )
            except Exception as e:  # noqa: BLE001
                _log.warning("[meet] closeEvent: end_meeting error: %s", e)
        if self._poll_timer:
            self._poll_timer.stop()
        super().closeEvent(event)


# ────────────────────────────────────────────────────────────────────────
# Background workers
# ────────────────────────────────────────────────────────────────────────


class _CreateWorker(QThread):
    done = pyqtSignal(object)  # Meeting
    failed = pyqtSignal(str)

    def __init__(self, config: Config, host_name: str, title: str) -> None:
        super().__init__()
        self._config = config
        self._host_name = host_name
        self._title = title or None

    def run(self) -> None:
        m = create_meeting(
            self._config.subunit_endpoint,
            self._config.subunit_api_key,
            host_name=self._host_name,
            host_email=self._config.account_email or None,
            title=self._title,
        )
        if m is None:
            self.failed.emit("Keine Verbindung zum Subunit-Server oder ungültiger API-Key.")
            return
        self.done.emit(m)


class _PollWorker(QThread):
    done = pyqtSignal(list)  # list[Participant]

    def __init__(self, config: Config, meeting: Meeting) -> None:
        super().__init__()
        self._config = config
        self._meeting = meeting

    def run(self) -> None:
        items = list_participants(
            self._config.subunit_endpoint,
            self._meeting.code,
            self._meeting.host_token,
        )
        self.done.emit(items or [])
