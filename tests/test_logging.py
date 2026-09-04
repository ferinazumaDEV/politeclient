"""Structured-logging tests: handler ownership and the documented output formats."""

from __future__ import annotations

import io
import json
import logging
import re

import pytest

from politeclient import PoliteClient
from politeclient.logging_utils import (
    HANDLER_NAME,
    LOGGER_NAME,
    _JsonFormatter,
    _KeyValueFormatter,
    get_logger,
)


@pytest.fixture
def clean_logger():
    """The shared ``politeclient`` logger with no handlers, restored afterwards.

    The logger is process-global, so every test starts from a blank slate and
    hands back whatever configuration earlier imports (or the demo) left.
    """
    logger = logging.getLogger(LOGGER_NAME)
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    saved_propagate = logger.propagate
    for handler in saved_handlers:
        logger.removeHandler(handler)
    logger.propagate = True
    logger.setLevel(logging.NOTSET)
    try:
        yield logger
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
        for handler in saved_handlers:
            logger.addHandler(handler)
        logger.setLevel(saved_level)
        logger.propagate = saved_propagate


def _own_handler(logger: logging.Logger) -> logging.Handler:
    matches = [h for h in logger.handlers if h.get_name() == HANDLER_NAME]
    assert len(matches) == 1, [h.get_name() for h in logger.handlers]
    return matches[0]


# --------------------------------------------------------------------------- #
# Handler ownership: politeclient only ever configures its own handler
# --------------------------------------------------------------------------- #
def test_get_logger_creates_one_named_handler_and_reconfigures_only_it(clean_logger):
    logger = get_logger("json")
    own = _own_handler(logger)
    assert isinstance(own.formatter, _JsonFormatter)
    assert logger.propagate is False

    # Switching formats reuses the same handler instead of stacking a new one.
    assert get_logger("kv") is logger
    assert _own_handler(logger) is own
    assert isinstance(own.formatter, _KeyValueFormatter)
    assert len(logger.handlers) == 1


def test_get_logger_leaves_a_user_installed_handler_alone(clean_logger):
    # The application configured the logger first, as it would when shipping
    # logs to its own aggregator. politeclient must not replace its formatter,
    # add a second handler, or flip propagation.
    user_handler = logging.StreamHandler()
    user_formatter = logging.Formatter("%(message)s")
    user_handler.setFormatter(user_formatter)
    clean_logger.addHandler(user_handler)

    logger = get_logger("json")

    assert logger.handlers == [user_handler]
    assert user_handler.formatter is user_formatter
    assert logger.propagate is True


def test_get_logger_reformats_its_own_handler_but_not_a_later_user_handler(clean_logger):
    logger = get_logger("kv")
    own = _own_handler(logger)

    user_handler = logging.StreamHandler()
    user_formatter = logging.Formatter("%(message)s")
    user_handler.setFormatter(user_formatter)
    logger.addHandler(user_handler)

    get_logger("json")
    assert isinstance(own.formatter, _JsonFormatter)
    assert user_handler.formatter is user_formatter
    assert logger.handlers == [own, user_handler]


# --------------------------------------------------------------------------- #
# Output formats, as the README documents them
# --------------------------------------------------------------------------- #
def _capture(logger: logging.Logger) -> io.StringIO:
    """Point politeclient's own handler at a buffer and make INFO visible."""
    buf = io.StringIO()
    _own_handler(logger).setStream(buf)
    logger.setLevel(logging.INFO)
    return buf


def _route_429_then_200(server) -> None:
    def handler(ctx):
        if ctx.count <= 1:
            return 429, {"Retry-After": "1"}, {"error": "slow down"}
        return 200, {}, {"ok": True}

    server.route("/x", handler)


KV_RETRY = re.compile(
    r"^\[warning\] event=retry method=GET url=\S+/x status=429 attempt=1 retry_after=1 sleep=1\.0$"
)
KV_REQUEST = re.compile(
    r"^\[info\] event=request method=GET url=\S+/x status=200 attempt=2 elapsed_ms=\d+(\.\d+)?$"
)


def test_kv_format_emits_the_documented_lines(server, clean_logger):
    _route_429_then_200(server)
    with PoliteClient(base_url=server.base_url, log_format="kv", sleep=lambda s: None) as client:
        buf = _capture(clean_logger)
        client.get("/x")

    lines = buf.getvalue().splitlines()
    assert len(lines) == 2, lines
    assert KV_RETRY.match(lines[0]), lines[0]
    assert KV_REQUEST.match(lines[1]), lines[1]


def test_json_format_emits_one_object_per_line(server, clean_logger):
    _route_429_then_200(server)
    with PoliteClient(base_url=server.base_url, log_format="json", sleep=lambda s: None) as client:
        buf = _capture(clean_logger)
        client.get("/x")

    records = [json.loads(line) for line in buf.getvalue().splitlines()]
    assert [r["event"] for r in records] == ["retry", "request"]
    retry, request = records
    assert retry == {
        "level": "warning",
        "event": "retry",
        "method": "GET",
        "url": server.url("/x"),
        "status": 429,
        "attempt": 1,
        "retry_after": "1",
        "sleep": 1.0,
    }
    assert {k: request[k] for k in ("level", "event", "method", "url", "status", "attempt")} == {
        "level": "info",
        "event": "request",
        "method": "GET",
        "url": server.url("/x"),
        "status": 200,
        "attempt": 2,
    }
    assert isinstance(request["elapsed_ms"], float)


def test_politeclient_log_env_var_selects_json_when_log_format_is_none(server, clean_logger, monkeypatch):
    monkeypatch.setenv("POLITECLIENT_LOG", "json")
    server.route("/ok", lambda ctx: (200, {}, {"ok": True}))
    with PoliteClient(base_url=server.base_url) as client:
        buf = _capture(clean_logger)
        client.get("/ok")

    (line,) = buf.getvalue().splitlines()
    record = json.loads(line)
    assert (record["level"], record["event"], record["status"]) == ("info", "request", 200)
    assert isinstance(_own_handler(clean_logger).formatter, _JsonFormatter)
