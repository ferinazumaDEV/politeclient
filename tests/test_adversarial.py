"""Adversarial inputs for the parts of politeclient that do arithmetic on caller-supplied numbers.

The 2026-09-12 external audit fuzzed typedout, scaffld and framesig and found seven bugs; it did
not fuzz this package, so "no findings" here meant "not looked at". These are the looks. Each test
states an invariant a caller is entitled to rely on, then feeds the code the inputs that a real
client hits at the edges: a retry policy configured for very many retries, a token bucket asked for
zero, negative or NaN tokens, a ``Retry-After`` header written by a server that does not read RFCs.

Written against the code as it was: the first run of this file failed on
``backoff_for(1024)`` (OverflowError), ``acquire(-10)`` (the bucket *gained* ten tokens),
``acquire(nan)`` (never returned) and ``RateLimit(rate=inf)`` (OverflowError on ``.capacity``).
The random-use test then found a fifth: with a deterministic clock, ``acquire`` could spin forever
when the refill landed one ULP short of the requested tokens and the next sleep was too small for
the clock to register.
"""
from __future__ import annotations

import contextlib
import math
import random
import signal

import pytest

from politeclient.exceptions import RateLimitConfigError
from politeclient.ratelimit import RateLimit, TokenBucket
from politeclient.retry import RetryPolicy, parse_retry_after


@contextlib.contextmanager
def must_finish_within(seconds: float):
    """Turn a hang into a failure. Without this, a regression in the wait loop would not fail
    the suite, it would stall it. SIGALRM only exists on POSIX; CI runs on Linux."""
    if not hasattr(signal, "SIGALRM"):
        yield
        return

    def _alarm(*_):
        raise TimeoutError(f"did not finish within {seconds}s")

    previous = signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


# --- retry: the backoff is a number, bounded, for every attempt --------------------------------- #

@pytest.mark.parametrize("attempt", [0, 1, 10, 63, 64, 100, 1023, 1024, 1100, 5000, 10**6])
def test_backoff_never_exceeds_max_backoff_and_never_raises(attempt):
    """A client configured for thousands of retries must sleep max_backoff, not crash.

    ``backoff_factor * 2 ** attempt`` is an exact integer in Python; converting it to a float
    overflows past attempt ~1024, so a long-running client with a generous ``max_retries`` used
    to die with OverflowError on the attempt it should simply have waited a minute for."""
    policy = RetryPolicy(max_retries=10**7, jitter=False, max_backoff=60.0)
    delay = policy.backoff_for(attempt)
    assert 0.0 <= delay <= 60.0
    assert math.isfinite(delay)


def test_backoff_with_jitter_stays_inside_the_cap_for_every_attempt():
    policy = RetryPolicy(max_retries=10**7, jitter=True, max_backoff=7.5)
    rng = random.Random(20260914)
    for attempt in range(0, 3000, 7):
        delay = policy.backoff_for(attempt, rng=rng)
        assert 0.0 <= delay <= 7.5, attempt


@pytest.mark.parametrize("kwargs", [
    dict(max_retries=-1), dict(backoff_factor=float("nan")), dict(backoff_factor=float("inf")),
    dict(backoff_factor=-0.5), dict(max_backoff=float("nan")), dict(max_backoff=float("inf")),
    dict(max_backoff=-1.0),
])
def test_retry_policy_rejects_configuration_that_cannot_produce_a_sleep(kwargs):
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)


def test_compute_delay_is_bounded_whatever_the_server_says():
    policy = RetryPolicy(max_backoff=30.0)
    for header in ("99999999999999999999999", "9" * 400, "9" * 5000,
                   "Thu, 01 Jan 2099 00:00:00 GMT", "-5", "0", ""):
        delay = policy.compute_delay(2000, retry_after=header, now=0.0)
        assert 0.0 <= delay <= 30.0, header
        assert math.isfinite(delay)


# --- Retry-After: RFC 9110 delay-seconds is ASCII digits, nothing else -------------------------- #

@pytest.mark.parametrize("value,expected", [
    ("120", 120.0),
    (" 120 ", 120.0),
    ("0", 0.0),
    ("Thu, 01 Jan 1970 00:00:10 GMT", 10.0),
    ("Thu, 01 Jan 1970 00:00:00 GMT", 0.0),   # in the past: no wait, not a negative wait
])
def test_retry_after_well_formed(value, expected):
    assert parse_retry_after(value, now=0.0) == expected


def test_retry_after_with_more_digits_than_a_float_holds_means_never_not_a_crash():
    assert parse_retry_after("9" * 400, now=0.0) == math.inf
    assert parse_retry_after("9" * 5000, now=0.0) == math.inf


@pytest.mark.parametrize("value", [
    "+5", "-5",    # a sign is not a DIGIT
    "٣",      # ARABIC-INDIC DIGIT THREE: int() accepts it, RFC 9110 does not
    "１２",         # fullwidth digits, same story
    "1e3", "0x10", "1.5", "5s", "five", "", "   ", "Retry-After: 5",
])
def test_retry_after_rejects_what_the_rfc_does_not_allow(value):
    assert parse_retry_after(value, now=0.0) is None


def test_retry_after_never_raises_on_garbage():
    rng = random.Random(7)
    alphabet = "0123456789 :,-+.eEabcGMTxyz٣\t"
    for _ in range(3000):
        junk = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 24)))
        result = parse_retry_after(junk, now=0.0)
        assert result is None or (math.isfinite(result) and result >= 0.0), junk


# --- token bucket: tokens only ever go down when acquired, never above capacity ----------------- #

def _bucket(capacity=2, rate=1.0):
    clock = {"t": 0.0}
    return TokenBucket(rate, capacity, clock=lambda: clock["t"], sleep=lambda s: clock.__setitem__("t", clock["t"] + s)), clock


@pytest.mark.parametrize("tokens", [0, -1, -10.0, float("nan"), float("-inf")])
def test_acquire_rejects_non_positive_or_non_finite_tokens(tokens):
    """Negative tokens used to *refill* the bucket past its capacity (a limiter that can be
    bypassed by asking for -10), and NaN never returned at all."""
    bucket, _ = _bucket()
    before = bucket.available
    with must_finish_within(2.0), pytest.raises((RateLimitConfigError, ValueError)):
        bucket.acquire(tokens)
    assert bucket.available == before


def test_acquire_more_than_capacity_is_rejected_including_infinity():
    bucket, _ = _bucket(capacity=3)
    for tokens in (3.5, 10, float("inf")):
        with pytest.raises(RateLimitConfigError):
            bucket.acquire(tokens)


def test_available_never_exceeds_capacity_under_random_use_and_acquire_always_returns():
    """Seed 2026 is not arbitrary: at iteration ~1500 the refill lands 8.9e-16 tokens short of
    the request and the computed 4.4e-16 s sleep is absorbed by a clock reading 6.35, so the
    pre-fix loop never advanced. A deterministic clock is exactly what the ``clock``/``sleep``
    parameters exist for, so this is a supported configuration, not a test artefact."""
    bucket, clock = _bucket(capacity=5, rate=2.0)
    rng = random.Random(2026)
    with must_finish_within(10.0):
        for _ in range(2000):
            if rng.random() < 0.5:
                clock["t"] += rng.uniform(0, 4)
            else:
                bucket.acquire(rng.uniform(0.01, 5.0))
            assert 0.0 <= bucket.available <= 5.0 + 1e-9


def test_acquire_terminates_and_reports_the_wait():
    bucket, clock = _bucket(capacity=1, rate=4.0)   # 4 tokens/s, capacity 1
    assert bucket.acquire() == 0.0                   # the free token
    with must_finish_within(2.0):
        waited = bucket.acquire()                    # must wait 0.25 s for the next
    assert 0.24 <= waited <= 0.26
    assert clock["t"] >= 0.25


# --- RateLimit: configuration that is not a finite positive number is a configuration error ---- #

@pytest.mark.parametrize("kwargs", [
    dict(rate=float("inf")), dict(rate=float("nan")), dict(rate=5, per=float("inf")),
    dict(rate=5, per=float("nan")), dict(rate=0), dict(rate=-1), dict(rate=5, per=0),
    dict(rate=5, burst=float("inf")), dict(rate=5, burst=float("nan")),
])
def test_rate_limit_rejects_non_finite_or_non_positive_configuration(kwargs):
    with pytest.raises(RateLimitConfigError):
        RateLimit(**kwargs).capacity


@pytest.mark.parametrize("kwargs", [
    dict(rate_per_second=float("inf"), capacity=1), dict(rate_per_second=float("nan"), capacity=1),
    dict(rate_per_second=1.0, capacity=float("inf")), dict(rate_per_second=1.0, capacity=float("nan")),
])
def test_token_bucket_rejects_non_finite_construction(kwargs):
    with pytest.raises(RateLimitConfigError):
        TokenBucket(clock=lambda: 0.0, sleep=lambda s: None, **kwargs)


def test_rate_limit_capacity_is_an_int_for_any_finite_rate():
    for rate in (0.001, 0.5, 1, 1.0001, 7.9, 1e6, 1e15):
        cap = RateLimit(rate=rate).capacity
        assert isinstance(cap, int) and cap >= 1
