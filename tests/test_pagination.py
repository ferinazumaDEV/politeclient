"""Pagination-helper tests using an in-memory fake fetch."""

from __future__ import annotations

import pytest

from politeclient import dig, paginate_cursor, paginate_offset


def test_dig_nested_and_indices():
    data = {"a": {"b": [{"id": 1}, {"id": 2}]}}
    assert dig(data, "a.b.1.id") == 2
    assert dig(data, "a.missing") is None
    assert dig(data, "a.b.99") is None


def test_paginate_offset_stops_on_short_page():
    dataset = list(range(25))

    def fetch(params):
        offset = params["offset"]
        limit = params["limit"]
        return {"items": dataset[offset:offset + limit]}

    got = list(paginate_offset(fetch, items_key="items", limit=10))
    assert got == dataset


def test_paginate_offset_stops_on_empty_page_at_exact_multiple():
    # 20 items with limit 10: page 3 comes back empty and must stop the loop.
    dataset = list(range(20))
    calls = []

    def fetch(params):
        calls.append(params["offset"])
        return {"items": dataset[params["offset"]:params["offset"] + params["limit"]]}

    got = list(paginate_offset(fetch, items_key="items", limit=10))
    assert got == dataset
    assert calls == [0, 10, 20]  # third call returns [], loop terminates


def test_paginate_cursor_follows_next_until_gone():
    pages = {
        None: {"data": [1, 2], "paging": {"next": "c1"}},
        "c1": {"data": [3, 4], "paging": {"next": "c2"}},
        "c2": {"data": [5], "paging": {"next": None}},
    }

    def fetch(params):
        return pages[params.get("cursor")]

    got = list(paginate_cursor(fetch, items_key="data", cursor_key="paging.next"))
    assert got == [1, 2, 3, 4, 5]


def test_max_pages_safety_valve():
    def fetch(params):
        # A misbehaving API that always returns a full page and a next cursor.
        return {"data": [0] * 10, "next": "always"}

    got = list(paginate_cursor(fetch, items_key="data", cursor_key="next", max_pages=3))
    assert len(got) == 30


def test_items_key_none_uses_payload_as_list():
    def fetch(params):
        off = params["offset"]
        return list(range(off, min(off + 5, 12)))

    got = list(paginate_offset(fetch, limit=5))
    assert got == list(range(12))
