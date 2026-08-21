"""Disk-cache tests."""

from __future__ import annotations

import time

from politeclient import DiskCache


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


def test_corrupt_entry_is_a_miss(tmp_path):
    cache = DiskCache(tmp_path)
    key = DiskCache.make_key("GET", "https://x/y")
    (tmp_path / f"{key}.json").write_text("not json at all")
    assert cache.get(key) is None
