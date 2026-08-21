"""Retry policy: exponential backoff, jitter and ``Retry-After`` support.

Two things people forget when they write their own retry loop:

1. **Jitter.** If a thousand clients all back off by exactly ``2 ** attempt``
   seconds, they retry in lockstep and hammer the server in synchronised waves
   (the "thundering herd"). Adding randomness spreads them out. politeclient
   defaults to *full jitter* (AWS's recommendation): ``sleep = random(0, cap)``.
2. **Retry-After.** A well-behaved server tells you exactly when to come back
   via the ``Retry-After`` header (seconds, or an HTTP date). Ignoring it is
   both rude and counter-productive.
"""

from __future__ import annotations

import email.utils
import random
import time
from dataclasses import dataclass, field
from typing import FrozenSet, Optional


# Status codes worth retrying: 429 (rate limited) and the transient 5xx family.
DEFAULT_RETRY_STATUSES: FrozenSet[int] = frozenset({429, 500, 502, 503, 504})

# Only idempotent methods are retried by default; retrying a POST can create
# duplicate resources. Callers who know their POST is safe can opt in.
DEFAULT_RETRY_METHODS: FrozenSet[str] = frozenset({"GET", "HEAD", "PUT", "DELETE", "OPTIONS"})


@dataclass
class RetryPolicy:
    """How, and how often, to retry a failed request.

    Args:
        max_retries: Number of *additional* attempts after the first (so
            ``max_retries=3`` means up to 4 requests total).
        backoff_factor: Base multiplier. The uncapped backoff for attempt ``n``
            (0-indexed) is ``backoff_factor * 2 ** n``.
        max_backoff: Upper bound for any single sleep, in seconds.
        jitter: Apply full jitter to the computed backoff.
        respect_retry_after: Honour a ``Retry-After`` header when present,
            overriding the computed backoff.
        retry_statuses: HTTP status codes that trigger a retry.
        retry_methods: HTTP methods eligible for retry (upper-case).
    """

    max_retries: int = 3
    backoff_factor: float = 0.5
    max_backoff: float = 60.0
    jitter: bool = True
    respect_retry_after: bool = True
    retry_statuses: FrozenSet[int] = field(default_factory=lambda: DEFAULT_RETRY_STATUSES)
    retry_methods: FrozenSet[str] = field(default_factory=lambda: DEFAULT_RETRY_METHODS)

    def should_retry(self, method: str, status: int, attempt: int) -> bool:
        """Whether a response with ``status`` should be retried."""
        if attempt >= self.max_retries:
            return False
        if method.upper() not in self.retry_methods:
            return False
        return status in self.retry_statuses

    def backoff_for(self, attempt: int, *, rng: Optional[random.Random] = None) -> float:
        """Compute the backoff sleep (seconds) for a 0-indexed ``attempt``."""
        raw = self.backoff_factor * (2 ** attempt)
        capped = min(raw, self.max_backoff)
        if not self.jitter:
            return capped
        r = rng or random
        # Full jitter: a random point in [0, capped].
        return r.uniform(0.0, capped)

    def compute_delay(
        self,
        attempt: int,
        *,
        retry_after: Optional[str] = None,
        rng: Optional[random.Random] = None,
        now: Optional[float] = None,
    ) -> float:
        """Return how long to wait before the next attempt.

        If ``respect_retry_after`` is set and a valid ``Retry-After`` value is
        present, it wins over the computed backoff (clamped to ``max_backoff``).
        """
        if self.respect_retry_after and retry_after is not None:
            parsed = parse_retry_after(retry_after, now=now)
            if parsed is not None:
                return max(0.0, min(parsed, self.max_backoff))
        return self.backoff_for(attempt, rng=rng)


def parse_retry_after(value: str, *, now: Optional[float] = None) -> Optional[float]:
    """Parse a ``Retry-After`` header into seconds.

    The header comes in two flavours (RFC 9110): an integer number of seconds,
    or an HTTP date. Returns ``None`` if the value can't be understood.
    """
    value = value.strip()
    if not value:
        return None
    # Form 1: delay in seconds.
    try:
        return max(0.0, float(int(value)))
    except ValueError:
        pass
    # Form 2: an HTTP date. Depending on the Python version this either returns
    # None or raises on an unparseable value — handle both.
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    target = parsed.timestamp()
    current = now if now is not None else time.time()
    return max(0.0, target - current)
