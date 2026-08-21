"""A thread-safe token-bucket rate limiter.

The token bucket is the classic algorithm for "N requests per second, but let me
burst a little". Tokens drip into the bucket at a fixed rate up to a maximum
capacity; every request spends one token. When the bucket is empty, callers
block just long enough for the next token to arrive.

politeclient keeps one bucket per host so a slow, heavily rate-limited API never
starves requests to a fast one.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .exceptions import RateLimitConfigError


@dataclass(frozen=True)
class RateLimit:
    """Declarative rate-limit configuration.

    Args:
        rate: Sustained number of requests allowed per ``per`` seconds.
        per: Length of the window in seconds (defaults to 1.0, i.e. per second).
        burst: Maximum number of tokens that can accumulate, allowing short
            bursts above the sustained rate. Defaults to ``ceil(rate)`` so a
            fresh bucket can fire one window's worth of requests immediately.

    Example:
        >>> RateLimit(rate=5)            # 5 requests/second
        >>> RateLimit(rate=100, per=60)  # 100 requests/minute
        >>> RateLimit(rate=2, burst=10)  # 2/s sustained, bursts up to 10
    """

    rate: float
    per: float = 1.0
    burst: Optional[int] = None

    def __post_init__(self) -> None:
        if self.rate <= 0:
            raise RateLimitConfigError("rate must be > 0")
        if self.per <= 0:
            raise RateLimitConfigError("per must be > 0")
        if self.burst is not None and self.burst < 1:
            raise RateLimitConfigError("burst must be >= 1")

    @property
    def tokens_per_second(self) -> float:
        return self.rate / self.per

    @property
    def capacity(self) -> int:
        if self.burst is not None:
            return self.burst
        # One window's worth of requests, at least 1.
        import math

        return max(1, math.ceil(self.rate))


class TokenBucket:
    """A thread-safe token bucket.

    The bucket refills lazily: instead of running a background thread, it
    computes how many tokens *would* have dripped in since the last call every
    time :meth:`acquire` runs. This keeps it cheap and free of background
    machinery.
    """

    def __init__(
        self,
        rate_per_second: float,
        capacity: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_second <= 0:
            raise RateLimitConfigError("rate_per_second must be > 0")
        if capacity < 1:
            raise RateLimitConfigError("capacity must be >= 1")
        self._rate = rate_per_second
        self._capacity = float(capacity)
        self._tokens = float(capacity)
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, config: RateLimit, **kwargs: object) -> "TokenBucket":
        return cls(config.tokens_per_second, config.capacity, **kwargs)  # type: ignore[arg-type]

    def _refill_locked(self) -> None:
        now = self._clock()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last = now

    def acquire(self, tokens: float = 1.0) -> float:
        """Block until ``tokens`` are available, then consume them.

        Returns the number of seconds spent waiting (0.0 if a token was free).
        """
        if tokens > self._capacity:
            raise RateLimitConfigError(
                f"cannot acquire {tokens} tokens from a bucket of capacity {self._capacity}"
            )
        waited = 0.0
        while True:
            with self._lock:
                self._refill_locked()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return waited
                deficit = tokens - self._tokens
                wait = deficit / self._rate
            waited += wait
            self._sleep(wait)

    @property
    def available(self) -> float:
        """Current token count (refilled to *now*). Handy for tests/metrics."""
        with self._lock:
            self._refill_locked()
            return self._tokens
