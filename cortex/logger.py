"""Structured logging for Cortex.

Writes to both the console (coloured) and a rotating JSON-lines file at
logs/cortex.jsonl. The file is capped at 10 MB x 5 rotations = 50 MB max.

Usage:
    from cortex.logger import log
    log.info("pipeline started", extra={"video": "foo.mp4"})
    log.error("stage failed", exc_info=True)
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import time
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_LOG_FILE = _LOG_DIR / "cortex.jsonl"


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per line -- easy to tail and grep."""

    def format(self, record: logging.LogRecord) -> str:
        doc: dict = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        extra_keys = {
            k: v for k, v in record.__dict__.items()
            if k not in logging.LogRecord.__dict__ and not k.startswith("_")
               and k not in ("msg", "args", "exc_info", "exc_text", "stack_info",
                             "lineno", "funcName", "filename", "module", "pathname",
                             "name", "levelname", "levelno", "created", "msecs",
                             "relativeCreated", "thread", "threadName", "process",
                             "processName", "taskName", "message")
        }
        if extra_keys:
            doc.update(extra_keys)
        return json.dumps(doc, default=str)


class _ColourFormatter(logging.Formatter):
    _COLOURS = {
        "DEBUG":    "\033[36m",   # cyan
        "INFO":     "\033[32m",   # green
        "WARNING":  "\033[33m",   # yellow
        "ERROR":    "\033[31m",   # red
        "CRITICAL": "\033[35m",   # magenta
    }
    _RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        colour = self._COLOURS.get(record.levelname, "")
        reset = self._RESET if colour else ""
        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        prefix = f"{colour}[{ts} {record.levelname[:3]}]{reset}"
        msg = record.getMessage()
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)
        return f"{prefix} {msg}"


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("cortex")
    logger.setLevel(logging.DEBUG)

    # Console -- INFO and above, coloured
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    # Only colour if the terminal supports it
    if sys.stdout.isatty() or os.environ.get("FORCE_COLOR"):
        ch.setFormatter(_ColourFormatter())
    else:
        ch.setFormatter(logging.Formatter("[%(asctime)s %(levelname)s] %(message)s",
                                          datefmt="%H:%M:%S"))
    logger.addHandler(ch)

    # Rotating JSON file -- DEBUG and above
    fh = logging.handlers.RotatingFileHandler(
        _LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(_JsonFormatter())
    logger.addHandler(fh)

    return logger


log = _build_logger()
LOG_FILE = _LOG_FILE
