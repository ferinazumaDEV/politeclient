"""Tests for the @polite decorator."""

from __future__ import annotations

from politeclient import RetryPolicy, polite


def test_polite_injects_client_and_retries(server):
    def handler(ctx):
        if ctx.count <= 1:
            return 503, {}, "warming up"
        return 200, {}, {"ok": True}

    server.route("/resource", handler)

    recorded = []

    @polite(base_url=server.base_url, retry=RetryPolicy(max_retries=3, backoff_factor=0.0),
            sleep=recorded.append)
    def fetch(client, path):
        return client.get(path).json()

    result = fetch("/resource")
    assert result == {"ok": True}
    assert server.hit_count("/resource") == 2
    # The live client is reachable for inspection/cleanup.
    assert fetch.client.base_url == server.base_url
    fetch.client.close()
