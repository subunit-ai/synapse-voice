"""Render Synapse Voice widgets to PNG for review without needing a live session."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

OUT = Path(__file__).resolve().parent / "shots"
OUT.mkdir(parents=True, exist_ok=True)

NIGHT_BG = QColor(8, 16, 28)


def shot_widget(widget, name: str, padding: int = 24) -> Path:
    widget.adjustSize()
    widget.repaint()
    QApplication.processEvents()

    inner = widget.grab()
    canvas = QPixmap(inner.width() + padding * 2, inner.height() + padding * 2)
    canvas.fill(NIGHT_BG)
    p = QPainter(canvas)
    p.drawPixmap(padding, padding, inner)
    p.end()

    path = OUT / f"{name}.png"
    canvas.save(str(path))
    print(f"  → {path}")
    return path


def main() -> int:
    app = QApplication(sys.argv)

    from synapse_voice.config import Config
    from synapse_voice.ui.bubble import Bubble
    from synapse_voice.ui.history import HistoryDialog
    from synapse_voice.ui.settings import SettingsDialog

    cfg = Config()
    cfg.openrouter_api_key = "sk-or-***hidden***"
    # Seed history with a few entries
    cfg.history = [
        {
            "ts": "2026-05-07T18:42:11+00:00",
            "text": "Lass uns die Synapse Voice App so bauen, dass sie als Standalone .exe auf Windows läuft und als AppImage auf Debian.",
            "mode": "local",
            "paste_mode": "pasted",
            "target": "Telegram Desktop",
        },
        {
            "ts": "2026-05-07T19:14:02+00:00",
            "text": "Phase 2 sollte den Audio-Waveform-Indicator und Smooth-Fade enthalten.",
            "mode": "openrouter",
            "paste_mode": "pasted",
            "target": "VS Code",
        },
        {
            "ts": "2026-05-07T20:01:55+00:00",
            "text": "Trading Crypto stock-breakout-v7 Walk-Forward 5 von 5 bestätigt. Sharpe 3.4 bis 4.0, WR 58 Prozent.",
            "mode": "local",
            "paste_mode": "clipboard",
            "target": None,
        },
        {
            "ts": "2026-05-07T20:25:44+00:00",
            "text": "Heute super produktiver Tag.",
            "mode": "local",
            "paste_mode": "pasted",
            "target": "Notion",
        },
    ]

    print("Rendering bubble states...")
    # Bubble — 4 states
    b = Bubble()
    # Force opacity full so the offscreen grab isn't transparent
    b._opacity_effect.setOpacity(1.0)

    b.show_state("recording", "● Rec → VS Code", anchor_to_cursor=False)
    # Inject a fake meter pattern (rising waveform) for visual punch
    b._meter_history = [0.10, 0.18, 0.30, 0.45, 0.55, 0.65, 0.75, 0.80, 0.78, 0.65, 0.55, 0.42, 0.35, 0.40, 0.55, 0.70, 0.62, 0.50]
    b.repaint()
    shot_widget(b, "bubble-recording")

    b.show_state("transcribing", "… transcribing (local)", anchor_to_cursor=False)
    b._meter_history = [0.40, 0.55, 0.70, 0.80, 0.75, 0.60, 0.45, 0.30, 0.40, 0.55, 0.70, 0.80, 0.75, 0.60, 0.45, 0.30, 0.40, 0.55]
    b.repaint()
    shot_widget(b, "bubble-transcribing")

    b.show_state("done", "✓ pasted → Telegram Desktop", auto_hide_ms=0, anchor_to_cursor=False)
    shot_widget(b, "bubble-done")

    b.show_state("error", "⚠ no audio captured", auto_hide_ms=0, anchor_to_cursor=False)
    shot_widget(b, "bubble-error")

    print("Rendering settings dialog...")
    s = SettingsDialog(cfg)
    s.resize(440, 480)
    shot_widget(s, "settings", padding=12)

    print("Rendering history dialog...")
    h = HistoryDialog(cfg, on_repaste=lambda _t: None)
    h.resize(680, 460)
    shot_widget(h, "history", padding=12)

    print("\nAll shots saved to:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
