"""politeclient — a polite, careful, well-behaved HTTP client for Python.

A thin, well-behaved wrapper around ``requests`` that bundles everything people
forget when calling APIs: retries with backoff + jitter, ``Retry-After``
support, a per-host rate-limit governor, sane default headers, an optional disk
cache, pagination helpers, sensible timeouts and structured logging.

    from politeclient import PoliteClient, RateLimit, RetryPolicy

    with PoliteClient(rate_limit=RateLimit(rate=5)) as client:
        data = client.get("https://api.example.com/things").json()
"""

from __future__ import annotations

__version__ = "0.1.0"

from .cache import CachedResponse, DiskCache
from .client import DEFAULT_USER_AGENT, PoliteClient
from .decorator import polite
from .exceptions import (
    PoliteError,
    RateLimitConfigError,
    RetryBudgetExceeded,
)
from .pagination import dig, paginate_cursor, paginate_offset
from .ratelimit import RateLimit, TokenBucket
from .retry import RetryPolicy, parse_retry_after

__all__ = [
    "__version__",
    "PoliteClient",
    "RateLimit",
    "RetryPolicy",
    "TokenBucket",
    "DiskCache",
    "CachedResponse",
    "polite",
    "paginate_offset",
    "paginate_cursor",
    "dig",
    "parse_retry_after",
    "PoliteError",
    "RetryBudgetExceeded",
    "RateLimitConfigError",
    "DEFAULT_USER_AGENT",
]
