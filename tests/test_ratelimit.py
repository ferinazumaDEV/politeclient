"""Token-bucket tests with an injected fake clock — fully deterministic."""

from __future__ import annotations

import pytest

from politeclient import RateLimit, TokenBucket
from politeclient.exceptions import RateLimitConfigError


class FakeClock:
    """A controllable clock whose ``sleep`` advances its own time."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def test_burst_then_throttle():
    clock = FakeClock()
    # 2 tokens/sec, capacity 2. Two immediate calls, then throttled to 0.5s each.
    bucket = TokenBucket(2.0, 2, clock=clock.now, sleep=clock.sleep)

    assert bucket.acquire() == 0.0  # token 1, free
    assert bucket.acquire() == 0.0  # token 2, free (burst)

    waited = bucket.acquire()  # bucket empty -> wait for a refill
    assert waited == pytest.approx(0.5)  # 1 token / 2 per sec
    assert clock.sleeps == [pytest.approx(0.5)]


def test_refill_is_capped_at_capacity():
    clock = FakeClock()
    bucket = TokenBucket(1.0, 3, clock=clock.now, sleep=clock.sleep)
    # Drain, then let a long time pass; tokens must not exceed capacity.
    for _ in range(3):
        bucket.acquire()
    clock.t += 100.0
    assert bucket.available == pytest.approx(3.0)


def test_from_config_defaults_capacity_to_ceil_rate():
    bucket = TokenBucket.from_config(RateLimit(rate=5))
    assert bucket.available == pytest.approx(5.0)


def test_from_config_per_minute():
    cfg = RateLimit(rate=120, per=60)  # 2/sec sustained
    assert cfg.tokens_per_second == pytest.approx(2.0)


def test_invalid_configs():
    with pytest.raises(RateLimitConfigError):
        RateLimit(rate=0)
    with pytest.raises(RateLimitConfigError):
        RateLimit(rate=1, per=0)
    with pytest.raises(RateLimitConfigError):
        RateLimit(rate=1, burst=0)
    with pytest.raises(RateLimitConfigError):
        TokenBucket(1.0, 1).acquire(5)  # more than capacity
