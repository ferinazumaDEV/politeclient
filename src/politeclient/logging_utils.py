"""Structured logging helpers.

Every notable event (request, retry, cache hit, rate-limit wait) is logged as a
single record carrying structured fields, so the output is greppable and can be
shipped straight into a log aggregator. Set the environment variable
``POLITECLIENT_LOG=json`` (or pass ``log_format="json"`` to the client) to emit
newline-delimited JSON instead of the default human-readable ``key=value`` line.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

LOGGER_NAME = "politeclient"

#: Name stamped on the one handler politeclient installs itself, so it can be
#: told apart from handlers the application attached to the same logger.
HANDLER_NAME = "politeclient"


class _KeyValueFormatter(logging.Formatter):
    """Render ``record.event`` + ``record.fields`` as ``k=v`` pairs."""

    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", record.getMessage())
        fields: Dict[str, Any] = getattr(record, "fields", {})
        parts = [f"[{record.levelname.lower()}]", f"event={event}"]
        for key, value in fields.items():
            parts.append(f"{key}={_render(value)}")
        return " ".join(parts)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "level": record.levelname.lower(),
            "event": getattr(record, "event", record.getMessage()),
        }
        payload.update(getattr(record, "fields", {}))
        return json.dumps(payload, default=str, separators=(",", ":"))


def _render(value: Any) -> str:
    text = str(value)
    if any(c.isspace() for c in text):
        return json.dumps(text)
    return text


def get_logger(log_format: str | None = None) -> logging.Logger:
    """Return the shared ``politeclient`` logger, configured once.

    politeclient only ever configures its *own* handler. When the logger has no
    handlers at all, one :class:`logging.StreamHandler` named
    :data:`HANDLER_NAME` is attached and propagation is switched off. A handler
    the application attached itself is never touched — its formatter, level and
    the logger's propagation stay exactly as the application configured them —
    so ``log_format`` only has an effect on the politeclient handler.

    Args:
        log_format: ``"json"`` or ``"kv"``. Falls back to the
            ``POLITECLIENT_LOG`` env var, then to ``"kv"``.
    """
    logger = logging.getLogger(LOGGER_NAME)
    fmt = (log_format or os.environ.get("POLITECLIENT_LOG") or "kv").lower()
    formatter: logging.Formatter = _JsonFormatter() if fmt == "json" else _KeyValueFormatter()

    own = next((h for h in logger.handlers if h.get_name() == HANDLER_NAME), None)
    if own is None and not logger.handlers:
        own = logging.StreamHandler()
        own.set_name(HANDLER_NAME)
        logger.addHandler(own)
        logger.propagate = False
    # Reconfigure *our* handler idempotently so repeated construction is cheap
    # and switching formats works; foreign handlers are left as found.
    if own is not None:
        own.setFormatter(formatter)
    return logger


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """Emit one structured record.

    The ``event`` names what happened; the keyword ``fields`` carry the data.
    """
    if logger.isEnabledFor(level):
        logger.log(level, event, extra={"event": event, "fields": fields})
