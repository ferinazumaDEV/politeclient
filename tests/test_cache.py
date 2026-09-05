"""Disk-cache tests."""

from __future__ import annotations

import email.utils
import json
import time
from unittest import mock

from politeclient import DiskCache
from politeclient.cache import server_freshness


def test_set_then_get_roundtrip(tmp_path):
    cache = DiskCache(tmp_path)
    key = DiskCache.make_key("GET", "https://x/y", {"a": 1})
    cache.set(key, status_code=200, headers={"Content-Type": "application/json"},
              content=b'{"ok": true}', url="https://x/y")
    hit = cache.get(key)
    assert hit is not None
    assert hit.status_code == 200
    assert hit.json() == {"ok": True}
    assert hit.text == '{"ok": true}'


def test_key_is_order_independent_for_params():
    k1 = DiskCache.make_key("GET", "https://x/y", {"a": 1, "b": 2})
    k2 = DiskCache.make_key("GET", "https://x/y", {"b": 2, "a": 1})
    assert k1 == k2
    k3 = DiskCache.make_key("GET", "https://x/y", {"a": 1})
    assert k3 != k1


def test_expiry_removes_entry(tmp_path):
    cache = DiskCache(tmp_path, ttl=0.05)
    key = DiskCache.make_key("GET", "https://x/y")
    cache.set(key, status_code=200, headers={}, content=b"hi", url="https://x/y")
    assert cache.get(key) is not None
    time.sleep(0.06)
    assert cache.get(key) is None
    # File should have been cleaned up on the expired read.
    assert list(tmp_path.glob("*.json")) == []


def test_per_call_ttl_override(tmp_path):
    cache = DiskCache(tmp_path, ttl=3600)
    key = DiskCache.make_key("GET", "https://x/y")
    cache.set(key, status_code=200, headers={}, content=b"hi", url="https://x/y")
    # Override with an already-expired TTL.
    assert cache.get(key, ttl=0.0) is None


def test_clear(tmp_path):
    cache = DiskCache(tmp_path)
    for i in range(3):
        cache.set(DiskCache.make_key("GET", f"https://x/{i}"),
                  status_code=200, headers={}, content=b"x", url="")
    assert cache.clear() == 3
    assert cache.get(DiskCache.make_key("GET", "https://x/0")) is None


def test_clear_leaves_unrelated_files_alone(tmp_path):
    # Entries are always <64 hex>.json; other files sharing the directory —
    # even other JSON — are not the cache's to delete.
    cache = DiskCache(tmp_path)
    (tmp_path / "my-unrelated-config.json").write_text('{"keep": true}', encoding="utf-8")
    (tmp_path / "notes.txt").write_text("keep", encoding="utf-8")
    for i in range(2):
        cache.set(DiskCache.make_key("GET", f"https://x/{i}"),
                  status_code=200, headers={}, content=b"x", url="")
    assert cache.clear() == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["my-unrelated-config.json", "notes.txt"]
    assert (tmp_path / "my-unrelated-config.json").read_text(encoding="utf-8") == '{"keep": true}'


def test_corrupt_entry_is_a_miss(tmp_path):
    cache = DiskCache(tmp_path)
    key = DiskCache.make_key("GET", "https://x/y")
    (tmp_path / f"{key}.json").write_text("not json at all")
    assert cache.get(key) is None


# --------------------------------------------------------------------------- #
# What reaches disk: an allowlist, and nothing else
# --------------------------------------------------------------------------- #
def _entry(tmp_path):
    """The single cache file in ``tmp_path``, as raw text and as parsed JSON."""
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    raw = files[0].read_text(encoding="utf-8")
    return raw, json.loads(raw)


def test_set_cookie_never_reaches_disk(tmp_path):
    cache = DiskCache(tmp_path)
    key = DiskCache.make_key("GET", "https://x/y")
    cache.set(
        key,
        status_code=200,
        headers={
            "Content-Type": "application/json",
            "Set-Cookie": "session=s3cr3t-token; HttpOnly",
            "Authorization": "Bearer s3cr3t-token",
            "WWW-Authenticate": "Basic realm=x",
        },
        content=b"{}",
        url="https://x/y",
    )
    raw, data = _entry(tmp_path)
    assert "s3cr3t-token" not in raw
    lowered = {k.lower() for k in data["headers"]}
    assert "set-cookie" not in lowered
    assert "authorization" not in lowered
    assert "www-authenticate" not in lowered
    # ...and the one useful header did survive.
    assert data["headers"]["Content-Type"] == "application/json"


def test_only_allowlisted_headers_are_stored(tmp_path):
    cache = DiskCache(tmp_path)
    key = DiskCache.make_key("GET", "https://x/y")
    cache.set(
        key,
        status_code=200,
        headers={
            "content-type": "text/plain",
            "Content-Encoding": "gzip",
            "ETag": '"abc"',
            "Last-Modified": "Wed, 21 Oct 2015 07:28:00 GMT",
            "Date": "Wed, 21 Oct 2015 07:28:00 GMT",
            "Server": "nginx/1.2.3",
            "X-Request-Id": "42",
        },
        content=b"hi",
        url="https://x/y",
    )
    _, data = _entry(tmp_path)
    assert {k.lower() for k in data["headers"]} == {
        "content-type",
        "content-encoding",
        "etag",
        "last-modified",
        "date",
    }
    hit = cache.get(key)
    assert hit is not None
    assert hit.headers["ETag"] == '"abc"'


# --------------------------------------------------------------------------- #
# Cache-Control: no-store
# --------------------------------------------------------------------------- #
def test_no_store_response_is_not_written(tmp_path):
    cache = DiskCache(tmp_path)
    key = DiskCache.make_key("GET", "https://x/y")
    stored = cache.set(
        key,
        status_code=200,
        headers={"Cache-Control": "private, no-store, max-age=60"},
        content=b"secret",
        url="https://x/y",
    )
    assert stored is False
    assert list(tmp_path.glob("*.json")) == []
    assert cache.get(key) is None


def test_no_store_is_matched_case_insensitively(tmp_path):
    cache = DiskCache(tmp_path)
    key = DiskCache.make_key("GET", "https://x/y")
    assert cache.set(key, status_code=200, headers={"cache-control": "No-Store"},
                     content=b"x", url="https://x/y") is False


def test_storable_response_still_returns_true(tmp_path):
    cache = DiskCache(tmp_path)
    key = DiskCache.make_key("GET", "https://x/y")
    assert cache.set(key, status_code=200, headers={"Cache-Control": "max-age=600"},
                     content=b"x", url="https://x/y") is True


# --------------------------------------------------------------------------- #
# Vary
# --------------------------------------------------------------------------- #
def test_response_with_vary_is_not_written(tmp_path):
    cache = DiskCache(tmp_path)
    key = DiskCache.make_key("GET", "https://x/y")
    stored = cache.set(key, status_code=200, headers={"Vary": "Accept-Encoding"},
                       content=b"x", url="https://x/y")
    assert stored is False
    assert list(tmp_path.glob("*.json")) == []


def test_empty_vary_does_not_block_storage(tmp_path):
    cache = DiskCache(tmp_path)
    key = DiskCache.make_key("GET", "https://x/y")
    assert cache.set(key, status_code=200, headers={"Vary": "  "},
                     content=b"x", url="https://x/y") is True


def test_stored_entry_with_vary_is_never_served(tmp_path):
    # An entry written by hand (or by an older version) that carries a Vary must
    # not be replayed: the key has no headers in it, so we cannot match variants.
    cache = DiskCache(tmp_path)
    key = DiskCache.make_key("GET", "https://x/y")
    (tmp_path / f"{key}.json").write_text(
        json.dumps(
            {
                "status_code": 200,
                "headers": {"Vary": "Accept-Language"},
                "content": "aGk=",
                "url": "https://x/y",
                "created_at": time.time(),
            }
        ),
        encoding="utf-8",
    )
    assert cache.get(key) is None
    assert list(tmp_path.glob("*.json")) == []


# --------------------------------------------------------------------------- #
# Server-declared freshness caps the local TTL
# --------------------------------------------------------------------------- #
def test_server_freshness_reads_max_age_expires_and_age():
    assert server_freshness({}) is None
    assert server_freshness({"Cache-Control": "public, max-age=120"}) == 120.0
    # An Age from an upstream cache is subtracted, never added.
    assert server_freshness({"Cache-Control": "max-age=120", "Age": "50"}) == 70.0
    assert server_freshness({"Cache-Control": "max-age=10", "Age": "999"}) == 0.0
    # no-cache means "revalidate first"; we cannot, so it is never fresh.
    assert server_freshness({"Cache-Control": "no-cache"}) == 0.0
    # An unreadable max-age falls through to Expires (here: absent).
    assert server_freshness({"Cache-Control": "max-age=soon"}) is None
    # Expires in the past, and the invalid-but-common "Expires: 0", are expired.
    past = email.utils.formatdate(time.time() - 3600, usegmt=True)
    assert server_freshness({"Expires": past}) == 0.0
    assert server_freshness({"Expires": "0"}) == 0.0
    future = email.utils.formatdate(time.time() + 600, usegmt=True)
    assert 0 < server_freshness({"Expires": future}) <= 600


def test_server_max_age_caps_a_longer_local_ttl(tmp_path):
    cache = DiskCache(tmp_path, ttl=3600)
    key = DiskCache.make_key("GET", "https://x/y")
    cache.set(key, status_code=200, headers={"Cache-Control": "max-age=0"},
              content=b"hi", url="https://x/y")
    # Stored, but already stale by the server's own reckoning.
    assert cache.get(key) is None


def test_expires_caps_a_longer_local_ttl(tmp_path):
    cache = DiskCache(tmp_path, ttl=3600)
    key = DiskCache.make_key("GET", "https://x/y")
    past = email.utils.formatdate(time.time() - 60, usegmt=True)
    cache.set(key, status_code=200, headers={"Expires": past},
              content=b"hi", url="https://x/y")
    assert cache.get(key) is None


def test_shorter_local_ttl_still_wins(tmp_path):
    cache = DiskCache(tmp_path, ttl=0.05)
    key = DiskCache.make_key("GET", "https://x/y")
    cache.set(key, status_code=200, headers={"Cache-Control": "max-age=86400"},
              content=b"hi", url="https://x/y")
    assert cache.get(key) is not None
    time.sleep(0.06)
    assert cache.get(key) is None


def test_server_max_age_bounds_an_unlimited_local_ttl(tmp_path):
    cache = DiskCache(tmp_path, ttl=None)  # "never expire", locally
    key = DiskCache.make_key("GET", "https://x/y")
    cache.set(key, status_code=200, headers={"Cache-Control": "max-age=0"},
              content=b"hi", url="https://x/y")
    assert cache.get(key) is None


# A TTL of zero is only reachable now that the server's freshness bounds the
# local one, and "fresh for zero seconds" must never produce a hit. The clock is
# frozen so the assertion does not depend on the platform's timer resolution:
# on Linux time.time() advances between the write and the read, but on a coarse
# clock (~15.6 ms on Windows) both land in the same tick.


def test_zero_ttl_misses_within_a_single_clock_tick(tmp_path):
    cache = DiskCache(tmp_path, ttl=3600)
    key = DiskCache.make_key("GET", "https://x/y")
    with mock.patch("politeclient.cache.time.time", return_value=1000.0):
        cache.set(key, status_code=200, headers={"Cache-Control": "max-age=0"},
                  content=b"hi", url="https://x/y")
        assert cache.get(key) is None


def test_no_cache_misses_within_a_single_clock_tick(tmp_path):
    cache = DiskCache(tmp_path, ttl=3600)
    key = DiskCache.make_key("GET", "https://x/y")
    with mock.patch("politeclient.cache.time.time", return_value=1000.0):
        cache.set(key, status_code=200, headers={"Cache-Control": "no-cache"},
                  content=b"hi", url="https://x/y")
        assert cache.get(key) is None


def test_frozen_clock_still_serves_a_response_the_server_called_fresh(tmp_path):
    # Control for the two tests above: the miss comes from the zero TTL, not
    # from freezing the clock.
    cache = DiskCache(tmp_path, ttl=3600)
    key = DiskCache.make_key("GET", "https://x/y")
    with mock.patch("politeclient.cache.time.time", return_value=1000.0):
        cache.set(key, status_code=200, headers={"Cache-Control": "max-age=5"},
                  content=b"hi", url="https://x/y")
        assert cache.get(key) is not None


# --- credentials must never share a cache entry -------------------------------

def test_carries_credentials_is_case_insensitive():
    from politeclient.cache import carries_credentials

    assert carries_credentials({"Authorization": "Bearer a"}) is True
    assert carries_credentials({"authorization": "Bearer a"}) is True
    assert carries_credentials({"COOKIE": "sid=1"}) is True
    assert carries_credentials({"Accept": "application/json"}) is False
    assert carries_credentials(None) is False
    assert carries_credentials({}) is False


def test_authenticated_request_is_not_cached(tmp_path, monkeypatch):
    """Two callers with different tokens must not share one entry.

    The cache key is method + URL + params, so an authenticated response is
    unshareable by construction. Rather than put credentials in the key, the
    client skips the cache entirely for such requests.
    """
    from politeclient.cache import DiskCache
    from politeclient.client import PoliteClient

    cache = DiskCache(str(tmp_path / "c"))
    client = PoliteClient(cache=cache)

    calls = []

    class _Resp:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        content = b'{"who":"a"}'
        url = "https://example.test/me"
        encoding = "utf-8"
        reason = "OK"
        history = ()

        def raise_for_status(self):
            return None

    def _fake_send(self, request, **kwargs):  # noqa: ANN001
        calls.append(dict(request.headers))
        return _Resp()

    monkeypatch.setattr("requests.Session.send", _fake_send, raising=False)

    # Same URL, two different identities. Neither may be served from cache.
    client.request("GET", "https://example.test/me", headers={"Authorization": "Bearer a"})
    client.request("GET", "https://example.test/me", headers={"Authorization": "Bearer b"})

    assert len(calls) == 2, "the second identity was served a cached response"
    assert DiskCache.make_key("GET", "https://example.test/me", None) is not None
    assert cache.get(DiskCache.make_key("GET", "https://example.test/me", None)) is None


# --- CachedResponse: the value a cache hit is rebuilt from -------------------

def test_cached_response_accessors():
    from politeclient import CachedResponse

    cached = CachedResponse(
        status_code=200,
        headers={"Content-Type": "application/json"},
        content=b'{"a": 1}',
        url="https://x/y",
        created_at=time.time() - 5,
    )
    assert cached.json() == {"a": 1}
    assert cached.text == '{"a": 1}'
    assert 5 <= cached.age < 60
