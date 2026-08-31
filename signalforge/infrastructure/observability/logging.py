"""Structured JSON logging (REQ-025).

One line per event, machine-readable, carrying the request id so a single
request can be followed across the API, the worker and the bus. Two rules are
enforced by the formatter rather than left to callers: known-sensitive keys are
redacted whatever a caller passes, and a value that will not serialise is
stringified instead of taking the log line - and with it the diagnosis - away.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)

REDACTED = "[redacted]"
REDACTED_KEYS = frozenset(
    {
        "password", "new_password", "old_password",
        "token", "access_token", "refresh_token", "id_token", "invite_token",
        "secret", "secrets", "api_key", "apikey", "credential", "credentials",
        "authorization", "cookie", "set-cookie",
        "ciphertext", "fernet_key", "jwt_secret", "signature",
    }
)

_RESERVED = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
    "pathname", "process", "processName", "relativeCreated", "stack_info",
    "taskName", "thread", "threadName",
}


def new_request_id() -> str:
    return uuid4().hex[:16]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id

        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = REDACTED if key.lower() in REDACTED_KEYS else _safe(value)

        if record.exc_info:
            exc_type, exc, _ = record.exc_info
            payload["error"] = {
                "type": exc_type.__name__ if exc_type else "Exception",
                "message": str(exc),
                "stack": self.formatException(record.exc_info),
            }
        return json.dumps(payload, ensure_ascii=False, default=str)


def _safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {
            k: REDACTED if str(k).lower() in REDACTED_KEYS else _safe(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    return str(value)


ADOPTED_LOGGERS = ("uvicorn", "uvicorn.error", "sqlalchemy.engine", "alembic")
"""Third-party loggers that install their own plain-text handlers. They are
taken over so one process does not emit two log formats."""

SILENCED_LOGGERS = ("uvicorn.access",)
"""The server's access log duplicates the request line the middleware already
writes with a request id, a route template and a duration; one of the two has
to go, and the richer one stays."""


def configure_logging(level: str = "INFO", stream=None) -> None:
    """Replace the root handlers with one JSON handler. Called at import, again
    from the lifespan once the server has installed its own handlers, and by
    tests that want to read the lines back."""
    handler = logging.StreamHandler(stream) if stream is not None else logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    for name in ADOPTED_LOGGERS:
        adopted = logging.getLogger(name)
        adopted.handlers = []
        adopted.propagate = True
    for name in SILENCED_LOGGERS:
        silenced = logging.getLogger(name)
        silenced.handlers = []
        silenced.propagate = True
        silenced.setLevel(logging.WARNING)
