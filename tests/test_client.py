"""End-to-end client tests against the local mock server.

These exercise the real request path: retries, ``Retry-After``, the rate-limit
governor, the disk cache, default headers and pagination — all deterministic and
offline.
"""

from __future__ import annotations

import pytest

from politeclient import (
    PoliteClient,
    RateLimit,
    RetryBudgetExceeded,
    RetryPolicy,
)


class Recorder:
    """A no-op sleep that records requested durations (keeps tests instant)."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


# --------------------------------------------------------------------------- #
# Retries
# --------------------------------------------------------------------------- #
def test_retries_on_429_then_succeeds_and_honours_retry_after(server):
    def handler(ctx):
        if ctx.count <= 2:
            return 429, {"Retry-After": "2"}, {"error": "slow down"}
        return 200, {}, {"ok": True}

    server.route("/things", handler)
    sleeps = Recorder()
    with PoliteClient(base_url=server.base_url, sleep=sleeps) as client:
        resp = client.get("/things")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert server.hit_count("/things") == 3
    # Both retries waited exactly the Retry-After value (2s), not the backoff.
    assert sleeps.sleeps == [2.0, 2.0]


def test_retries_on_500_then_succeeds(server):
    def handler(ctx):
        if ctx.count <= 2:
            return 500, {}, "boom"
        return 200, {}, {"ok": True}

    server.route("/flaky", handler)
    sleeps = Recorder()
    policy = RetryPolicy(max_retries=5, backoff_factor=0.1, max_backoff=1.0)
    with PoliteClient(base_url=server.base_url, retry=policy, sleep=sleeps) as client:
        resp = client.get("/flaky")

    assert resp.status_code == 200
    assert server.hit_count("/flaky") == 3
    assert len(sleeps.sleeps) == 2  # two backoff waits between three attempts
    assert all(0.0 <= s <= 1.0 for s in sleeps.sleeps)


def test_gives_up_and_returns_last_response(server):
    server.route("/down", lambda ctx: (503, {}, "unavailable"))
    sleeps = Recorder()
    policy = RetryPolicy(max_retries=2, backoff_factor=0.01)
    with PoliteClient(base_url=server.base_url, retry=policy, sleep=sleeps) as client:
        resp = client.get("/down")

    assert resp.status_code == 503
    assert resp.from_cache is False
    assert server.hit_count("/down") == 3  # initial + 2 retries


def test_post_is_not_retried_by_default(server):
    server.route("/submit", lambda ctx: (500, {}, "boom"))
    sleeps = Recorder()
    with PoliteClient(base_url=server.base_url, sleep=sleeps) as client:
        resp = client.post("/submit", json={"a": 1})

    assert resp.status_code == 500
    assert server.hit_count("/submit") == 1  # no retry for POST
    assert sleeps.sleeps == []


def test_transport_error_raises_retry_budget_exceeded():
    # Nothing is listening on port 1 -> ConnectionError on every attempt.
    sleeps = Recorder()
    policy = RetryPolicy(max_retries=1, backoff_factor=0.01)
    with PoliteClient(base_url="http://127.0.0.1:1", retry=policy, sleep=sleeps,
                      timeout=1.0) as client:
        with pytest.raises(RetryBudgetExceeded) as excinfo:
            client.get("/anything")

    assert excinfo.value.attempts == 2  # initial + 1 retry
    assert len(sleeps.sleeps) == 1


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #
def test_rate_limit_governor_throttles(server):
    server.route("/ping", lambda ctx: (200, {}, {"pong": ctx.count}))
    clock = FakeClock()
    limit = RateLimit(rate=2, burst=1)  # 2/s sustained, no burst headroom
    with PoliteClient(base_url=server.base_url, rate_limit=limit,
                      sleep=clock.sleep, clock=clock.now) as client:
        for _ in range(3):
            assert client.get("/ping").status_code == 200

    # First request free (initial token); each of the next two waits 1/2 s.
    assert clock.sleeps == [pytest.approx(0.5), pytest.approx(0.5)]


# --------------------------------------------------------------------------- #
# Caching
# --------------------------------------------------------------------------- #
def test_get_cache_avoids_second_network_call(server, tmp_path):
    server.route("/data", lambda ctx: (200, {}, {"n": ctx.count}))
    with PoliteClient(base_url=server.base_url, cache=tmp_path) as client:
        first = client.get("/data")
        second = client.get("/data")

    assert first.from_cache is False
    assert second.from_cache is True
    assert first.json() == second.json() == {"n": 1}
    assert server.hit_count("/data") == 1  # second served from disk


def test_cache_can_be_bypassed_per_request(server, tmp_path):
    server.route("/data", lambda ctx: (200, {}, {"n": ctx.count}))
    with PoliteClient(base_url=server.base_url, cache=tmp_path) as client:
        client.get("/data")                      # populates cache (n=1)
        fresh = client.get("/data", use_cache=False)  # forces network (n=2)

    assert fresh.json() == {"n": 2}
    assert server.hit_count("/data") == 2


# --------------------------------------------------------------------------- #
# Headers
# --------------------------------------------------------------------------- #
def test_default_user_agent_is_not_python_requests(server):
    def echo(ctx):
        return 200, {}, {"ua": ctx.headers.get("user-agent", "")}

    server.route("/echo", echo)
    with PoliteClient(base_url=server.base_url) as client:
        ua = client.get("/echo").json()["ua"]

    assert ua.startswith("politeclient/")
    assert "python-requests" not in ua


def test_custom_user_agent_is_sent(server):
    server.route("/echo", lambda ctx: (200, {}, {"ua": ctx.headers.get("user-agent", "")}))
    with PoliteClient(base_url=server.base_url, user_agent="MyBot/9.9") as client:
        assert client.get("/echo").json()["ua"] == "MyBot/9.9"


# --------------------------------------------------------------------------- #
# Pagination
# --------------------------------------------------------------------------- #
def test_offset_pagination_end_to_end(server):
    dataset = list(range(23))

    def handler(ctx):
        off = int(ctx.query.get("offset", 0))
        lim = int(ctx.query.get("limit", 10))
        return 200, {}, {"items": dataset[off:off + lim]}

    server.route("/list", handler)
    with PoliteClient(base_url=server.base_url) as client:
        got = list(client.paginate_offset("/list", items_key="items", limit=10))

    assert got == dataset


def test_cursor_pagination_end_to_end(server):
    pages = {
        "": {"data": [1, 2], "next": "c1"},
        "c1": {"data": [3, 4], "next": "c2"},
        "c2": {"data": [5], "next": None},
    }

    def handler(ctx):
        return 200, {}, pages[ctx.query.get("cursor", "")]

    server.route("/feed", handler)
    with PoliteClient(base_url=server.base_url) as client:
        got = list(client.paginate_cursor("/feed", items_key="data", cursor_key="next"))

    assert got == [1, 2, 3, 4, 5]
