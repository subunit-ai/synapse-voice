"""Centralized logger.

Writes to %LOCALAPPDATA%\\synapse-voice\\logs\\synapse-voice.log on Windows or
~/.local/share/synapse-voice/logs/synapse-voice.log on Linux. Captures:
  - app lifecycle events (start, quit, version)
  - all transcriber errors (caught + uncaught)
  - paste / clipboard / hotkey failures
  - mic / recording errors
  - Qt warnings via the message handler

Designed so silent failures on a frozen GUI build are still diagnosable.
"""
from __future__ import annotations

import logging
import sys
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

_LOGGER_NAME = "synapse_voice"
_initialized = False
_log_file: Optional[Path] = None


def log_dir() -> Path:
    if sys.platform == "win32":
        base = Path.home() / "AppData" / "Local" / "synapse-voice" / "logs"
    else:
        base = Path.home() / ".local" / "share" / "synapse-voice" / "logs"
    base.mkdir(parents=True, exist_ok=True)
    return base


def log_file_path() -> Path:
    return log_dir() / "synapse-voice.log"


def init_logging(app_version: str) -> Path:
    """Set up the global logger. Idempotent — safe to call repeatedly."""
    global _initialized, _log_file
    if _initialized:
        return _log_file  # type: ignore[return-value]

    _log_file = log_file_path()

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Rotating file (5x 1MB)
    file_handler = RotatingFileHandler(
        _log_file, maxBytes=1_048_576, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    # Stderr (visible only when run from a console)
    if sys.stderr is not None:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setLevel(logging.INFO)
        stream_handler.setFormatter(fmt)
        logger.addHandler(stream_handler)

    # Uncaught exception hook — funnel to logger + write a banner so the user
    # can see "the app crashed at this line" if they ever open the file.
    def _excepthook(exctype, value, tb):
        logger.exception(
            "Uncaught exception",
            exc_info=(exctype, value, tb),
        )
        # Also write a fallback plain text in case logging itself is broken.
        try:
            with open(_log_file, "a", encoding="utf-8") as f:
                f.write(
                    f"\n=== uncaught {datetime.now(timezone.utc).isoformat()} ===\n"
                )
                import traceback as _tb

                _tb.print_exception(exctype, value, tb, file=f)
        except Exception:
            pass

    sys.excepthook = _excepthook

    # Threading uncaught exception hook (Python 3.8+)
    def _thread_excepthook(args):
        logger.error(
            "Uncaught thread exception in %s",
            getattr(args.thread, "name", "<thread>"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _thread_excepthook  # type: ignore[assignment]

    # Pipe Qt warnings/criticals into our logger so PyQt issues land in the file
    # instead of getting swallowed by a windowed (no-console) Win build.
    try:
        from PyQt6.QtCore import QtMsgType, qInstallMessageHandler

        _QT_LEVELS = {
            QtMsgType.QtDebugMsg: logging.DEBUG,
            QtMsgType.QtInfoMsg: logging.INFO,
            QtMsgType.QtWarningMsg: logging.WARNING,
            QtMsgType.QtCriticalMsg: logging.ERROR,
            QtMsgType.QtFatalMsg: logging.CRITICAL,
        }

        def _qt_message_handler(msg_type, _ctx, msg):
            logger.log(_QT_LEVELS.get(msg_type, logging.WARNING), "Qt: %s", msg)

        qInstallMessageHandler(_qt_message_handler)
    except Exception:
        pass

    logger.info("=" * 60)
    logger.info(
        "Sonar %s starting on %s (python %s)",
        app_version,
        sys.platform,
        sys.version.split()[0],
    )
    logger.info("Log file: %s", _log_file)

    _initialized = True
    return _log_file


def get(name: str = "") -> logging.Logger:
    """Get a child logger. Use module-name (`synapse_voice.foo`) by convention."""
    if name:
        return logging.getLogger(f"{_LOGGER_NAME}.{name}")
    return logging.getLogger(_LOGGER_NAME)
