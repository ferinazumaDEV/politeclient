"""Structured-logging tests: handler ownership and the documented output formats."""

from __future__ import annotations

import logging

import pytest

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
