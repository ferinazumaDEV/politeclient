"""Pagination helpers for the two shapes almost every API uses.

* **Offset/limit** — ``?limit=100&offset=200``. You walk forward until a short
  or empty page tells you you've reached the end.
* **Cursor** — the response hands you an opaque token for the next page
  (``next_cursor``, ``next``, ``paging.next`` …). You follow it until it's gone.

Both helpers are plain generators that lazily pull one page at a time, so you can
``break`` early or wrap them in ``itertools.islice`` without fetching the whole
dataset. They take a ``fetch`` callable rather than a client so they stay
trivially testable and don't care whether the transport is politeclient,
``requests`` or a fake.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional

# A fetch callable maps query params -> a decoded JSON body (usually a dict).
FetchFn = Callable[[Mapping[str, Any]], Any]


def dig(data: Any, path: str) -> Any:
    """Follow a dotted ``path`` into nested dicts/lists.

    ``dig({"a": {"b": [1, 2]}}, "a.b")`` -> ``[1, 2]``. List indices work too:
    ``dig(payload, "data.items.0.id")``. Returns ``None`` if the path breaks.
    """
    current = data
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, Mapping):
            current = current.get(part)
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def _extract_items(payload: Any, items_key: Optional[str]) -> List[Any]:
    if items_key is None:
        # The payload is expected to be the list itself.
        return list(payload) if isinstance(payload, (list, tuple)) else []
    items = dig(payload, items_key)
    if items is None:
        return []
    if isinstance(items, (list, tuple)):
        return list(items)
    return [items]


def paginate_offset(
    fetch: FetchFn,
    *,
    items_key: Optional[str] = None,
    limit: int = 100,
    limit_param: str = "limit",
    offset_param: str = "offset",
    start_offset: int = 0,
    extra_params: Optional[Mapping[str, Any]] = None,
    max_pages: Optional[int] = None,
) -> Iterator[Any]:
    """Yield items from an offset/limit paginated endpoint.

    Stops when a page returns fewer than ``limit`` items (the last page) or an
    empty page. ``max_pages`` is a safety valve against a misbehaving API that
    never shrinks a page.
    """
    offset = start_offset
    pages = 0
    base = dict(extra_params or {})
    while True:
        params = {**base, limit_param: limit, offset_param: offset}
        payload = fetch(params)
        items = _extract_items(payload, items_key)
        for item in items:
            yield item
        pages += 1
        if not items or len(items) < limit:
            return
        if max_pages is not None and pages >= max_pages:
            return
        offset += limit


def paginate_cursor(
    fetch: FetchFn,
    *,
    items_key: Optional[str] = None,
    cursor_key: str,
    cursor_param: str = "cursor",
    start_cursor: Optional[str] = None,
    extra_params: Optional[Mapping[str, Any]] = None,
    max_pages: Optional[int] = None,
) -> Iterator[Any]:
    """Yield items from a cursor-paginated endpoint.

    Args:
        cursor_key: Dotted path to the *next* cursor in the response body
            (e.g. ``"paging.next"`` or ``"next_cursor"``). Iteration stops when
            this is missing, ``None`` or empty.
        cursor_param: Query-param name used to send the cursor back.
        start_cursor: Optional cursor for the first request.
    """
    cursor = start_cursor
    pages = 0
    base = dict(extra_params or {})
    while True:
        params: Dict[str, Any] = dict(base)
        if cursor is not None:
            params[cursor_param] = cursor
        payload = fetch(params)
        for item in _extract_items(payload, items_key):
            yield item
        pages += 1
        next_cursor = dig(payload, cursor_key)
        if not next_cursor:
            return
        if max_pages is not None and pages >= max_pages:
            return
        cursor = next_cursor
