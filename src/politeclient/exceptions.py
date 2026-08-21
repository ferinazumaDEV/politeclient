"""Exception hierarchy for politeclient.

Everything the library raises inherits from :class:`PoliteError`, so callers can
catch the whole family with a single ``except`` while still being able to
distinguish the interesting cases.
"""

from __future__ import annotations

from typing import Optional


class PoliteError(Exception):
    """Base class for every error raised by politeclient."""


class RetryBudgetExceeded(PoliteError):
    """Raised when a request keeps failing after the retry budget is spent.

    Attributes:
        attempts: How many attempts were made in total.
        last_status: The last HTTP status code seen, if the failures were
            HTTP responses rather than transport errors.
        last_exception: The last transport-level exception seen, if any.
    """

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
        last_status: Optional[int] = None,
        last_exception: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_status = last_status
        self.last_exception = last_exception


class RateLimitConfigError(PoliteError):
    """Raised when a rate-limit configuration is nonsensical (e.g. rate <= 0)."""
