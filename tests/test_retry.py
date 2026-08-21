"""Retry-policy and Retry-After parsing tests."""

from __future__ import annotations

import random
from email.utils import formatdate

import pytest

from politeclient import RetryPolicy, parse_retry_after


def test_backoff_is_exponential_without_jitter():
    policy = RetryPolicy(backoff_factor=0.5, jitter=False, max_backoff=100)
    assert policy.backoff_for(0) == pytest.approx(0.5)
    assert policy.backoff_for(1) == pytest.approx(1.0)
    assert policy.backoff_for(2) == pytest.approx(2.0)
    assert policy.backoff_for(3) == pytest.approx(4.0)


def test_backoff_is_capped():
    policy = RetryPolicy(backoff_factor=1.0, jitter=False, max_backoff=5.0)
    assert policy.backoff_for(10) == pytest.approx(5.0)


def test_full_jitter_stays_within_bounds():
    policy = RetryPolicy(backoff_factor=1.0, jitter=True, max_backoff=8.0)
    rng = random.Random(1234)
    for attempt in range(5):
        cap = min(1.0 * 2 ** attempt, 8.0)
        for _ in range(50):
            delay = policy.backoff_for(attempt, rng=rng)
            assert 0.0 <= delay <= cap


def test_should_retry_respects_status_method_and_budget():
    policy = RetryPolicy(max_retries=2)
    assert policy.should_retry("GET", 503, attempt=0) is True
    assert policy.should_retry("GET", 200, attempt=0) is False
    # POST is not retried by default (not idempotent).
    assert policy.should_retry("POST", 503, attempt=0) is False
    # Budget exhausted.
    assert policy.should_retry("GET", 503, attempt=2) is False


def test_retry_after_seconds_wins_over_backoff():
    policy = RetryPolicy(backoff_factor=10, jitter=False, max_backoff=100)
    delay = policy.compute_delay(0, retry_after="3")
    assert delay == pytest.approx(3.0)


def test_retry_after_http_date():
    future = formatdate(timeval=1_000_000_060, usegmt=True)  # 60s after our "now"
    seconds = parse_retry_after(future, now=1_000_000_000)
    assert seconds == pytest.approx(60.0, abs=1.0)


def test_retry_after_is_clamped_to_max_backoff():
    policy = RetryPolicy(max_backoff=5.0)
    assert policy.compute_delay(0, retry_after="3600") == pytest.approx(5.0)


def test_parse_retry_after_garbage_returns_none():
    assert parse_retry_after("later please") is None
    assert parse_retry_after("") is None
