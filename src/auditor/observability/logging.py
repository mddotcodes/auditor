"""Structured logging for cloud log scrapers (CloudWatch, Cloud Logging, …).

Enable with::

    AUDIT_LOG_FORMAT=json

Human-readable text remains the default for local CLI use.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, TextIO

_LOG_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "auditor_log_context",
    default=None,
)

_CONFIGURED = False


def _current_context() -> dict[str, Any]:
    return dict(_LOG_CONTEXT.get() or {})


def get_log_format() -> str:
    """Return ``json`` or ``text`` from ``AUDIT_LOG_FORMAT`` (default ``text``)."""
    raw = (os.environ.get("AUDIT_LOG_FORMAT") or "text").strip().lower()
    if raw in {"json", "structured", "jsonl"}:
        return "json"
    return "text"


def log_context(**fields: Any) -> dict[str, Any]:
    """Merge fields into the current log context; returns the previous context.

    Prefer :func:`bound_log_context` for temporary scopes.
    """
    current = _current_context()
    current.update({k: v for k, v in fields.items() if v is not None})
    _LOG_CONTEXT.set(current)
    return current


@contextmanager
def bound_log_context(**fields: Any) -> Iterator[None]:
    """Temporarily bind structured fields onto all log records in this scope."""
    merged = {
        **_current_context(),
        **{k: v for k, v in fields.items() if v is not None},
    }
    token = _LOG_CONTEXT.set(merged)
    try:
        yield
    finally:
        _LOG_CONTEXT.reset(token)


class ContextFilter(logging.Filter):
    """Inject contextvars fields onto every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in _current_context().items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line on stdout — parseable without scraping human text."""

    # Standard LogRecord attributes we never dump as extras.
    _SKIP = frozenset(
        {
            "name",
            "msg",
            "args",
            "created",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "message",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "exc_info",
            "exc_text",
            "thread",
            "threadName",
            "taskName",
            "asctime",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key in self._SKIP or key.startswith("_"):
                continue
            if value is None:
                continue
            try:
                json.dumps(value, default=str)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = str(value)

        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )


def configure_logging(
    *,
    level: str | None = None,
    fmt: str | None = None,
    stream: TextIO | None = None,
    force: bool = False,
) -> str:
    """Configure root / auditor loggers once.

    Returns the effective format (``json`` or ``text``).
    """
    global _CONFIGURED
    if _CONFIGURED and not force:
        return get_log_format() if fmt is None else ("json" if fmt == "json" else "text")

    fmt_name = (fmt or get_log_format()).strip().lower()
    if fmt_name in {"structured", "jsonl"}:
        fmt_name = "json"
    if fmt_name not in {"json", "text"}:
        fmt_name = "text"

    level_name = (level or os.environ.get("AUDIT_LOG_LEVEL") or "info").upper()
    log_level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.addFilter(ContextFilter())
    if fmt_name == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(TextFormatter())

    root = logging.getLogger()
    # Replace handlers so uvicorn/default config does not double-log as text.
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # Keep noisy libraries quieter unless debugging.
    if log_level > logging.DEBUG:
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    _CONFIGURED = True
    return fmt_name
