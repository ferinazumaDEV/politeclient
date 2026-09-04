"""Optional on-disk cache for GET responses.

A dead-simple, dependency-free file cache keyed by request identity. It only
ever stores successful, cacheable GET responses and honours a TTL. This is *not*
a full HTTP caching layer (no ETag/Last-Modified revalidation) — it is the "I'm
iterating on a scraper and don't want to hammer the API every run" cache, which
is exactly the cache people hand-roll badly.

Because entries are plain JSON files in a directory the caller picks, the cache
is deliberately conservative about what it is willing to write and to reuse:

* only an **allowlist** of response headers reaches disk
  (:data:`CACHEABLE_RESPONSE_HEADERS`) — ``Set-Cookie``, ``Authorization`` and
  every other credential-bearing header is dropped;
* a response with ``Cache-Control: no-store`` is not written at all
  (RFC 9111 §5.2.2.5 applies that directive to private caches too);
* a response with a non-empty ``Vary`` is not written either, because this cache
  keys on method + URL + params and therefore cannot tell two variants apart
  (RFC 9111 §4.1);
* the server's declared freshness (``Cache-Control: max-age``, ``Expires``) is
  an **upper bound** on the local TTL — the shorter of the two wins.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, FrozenSet, Mapping, Optional

from .retry import parse_retry_after


#: Response headers that may be persisted. This is an allowlist on purpose: a
#: header nobody thought about is discarded rather than written to disk. In
#: particular ``Set-Cookie``, ``Authorization``, ``Proxy-Authorization`` and
#: ``WWW-Authenticate`` never reach a cache file.
CACHEABLE_RESPONSE_HEADERS: FrozenSet[str] = frozenset(
    {
        "content-type",
        "content-encoding",
        "etag",
        "last-modified",
        "date",
        "vary",
    }
)


def filter_response_headers(headers: Mapping[str, str]) -> Dict[str, str]:
    """Return only the headers this cache is willing to persist.

    Matching is case-insensitive; the original spelling of a kept header is
    preserved.
    """
    return {
        name: value
        for name, value in headers.items()
        if name.lower() in CACHEABLE_RESPONSE_HEADERS
    }


def _header(headers: Mapping[str, str], name: str) -> Optional[str]:
    """Case-insensitive header lookup (``headers`` may be a plain ``dict``)."""
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def parse_cache_control(headers: Mapping[str, str]) -> Dict[str, str]:
    """Parse ``Cache-Control`` into ``{directive: value}`` (values may be "")."""
    raw = _header(headers, "Cache-Control")
    if not raw:
        return {}
    directives: Dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, value = part.partition("=")
        directives[name.strip().lower()] = value.strip().strip('"')
    return directives


def _expires_in(value: str) -> float:
    """Seconds of freshness left according to an ``Expires`` header value.

    ``Expires`` only takes the HTTP-date form. RFC 9111 §5.3 says an invalid
    value (the common ``Expires: 0``, for instance) means *already expired*, so
    anything we cannot read as a date yields ``0.0``.
    """
    value = value.strip()
    if not value or value.lstrip("+-").isdigit():
        return 0.0
    seconds = parse_retry_after(value)  # same HTTP-date parsing as Retry-After
    return 0.0 if seconds is None else seconds


def server_freshness(headers: Mapping[str, str]) -> Optional[float]:
    """How long the *server* says this response stays fresh, in seconds.

    ``Cache-Control: max-age`` wins over ``Expires`` (RFC 9111 §4.2.1), and an
    ``Age`` header is subtracted from it so a response that was already sitting
    in an upstream cache does not get a full lifetime here. Returns ``None`` when
    the server declared nothing, in which case the local TTL is all we have.
    """
    directives = parse_cache_control(headers)
    if "no-cache" in directives:
        # We cannot revalidate, so "don't reuse without revalidating" can only
        # mean "never reuse".
        return 0.0
    if "max-age" in directives:
        try:
            lifetime = float(int(directives["max-age"]))
        except ValueError:
            lifetime = None
        if lifetime is not None:
            age = 0.0
            raw_age = _header(headers, "Age")
            if raw_age:
                try:
                    age = max(0.0, float(int(raw_age.strip())))
                except ValueError:
                    age = 0.0
            return max(0.0, lifetime - age)
    expires = _header(headers, "Expires")
    if expires is not None:
        return _expires_in(expires)
    return None


def is_storable(headers: Mapping[str, str]) -> Optional[str]:
    """Return ``None`` if the response may be stored, else why it may not.

    Two refusals, both about correctness rather than taste:

    * ``Cache-Control: no-store`` — RFC 9111 §5.2.2.5 binds private caches too.
    * a non-empty ``Vary`` — this cache keys on method + URL + params only, so it
      has no way to tell one variant from another. Storing such a response and
      replaying it for a different request is exactly what RFC 9111 §4.1
      forbids, and without revalidation the only safe move is not to store it.
    """
    if "no-store" in parse_cache_control(headers):
        return "no-store"
    vary = _header(headers, "Vary")
    if vary is not None and vary.strip():
        return "vary"
    return None


@dataclass
class CachedResponse:
    """A response reconstructed from the cache."""

    status_code: int
    headers: Dict[str, str]
    content: bytes
    url: str
    created_at: float

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.content)

    @property
    def age(self) -> float:
        return time.time() - self.created_at


class DiskCache:
    """Content-addressed cache stored as one JSON file per entry.

    Entries are unencrypted JSON in ``directory``; the caller owns that path and
    its permissions. Only the headers in :data:`CACHEABLE_RESPONSE_HEADERS` are
    written — see the module docstring for what is refused and why.

    Args:
        directory: Where to store entries (created if missing).
        ttl: Default time-to-live in seconds. ``None`` means never expire. The
            server's own ``max-age``/``Expires`` can only make it shorter.
    """

    def __init__(self, directory: str | os.PathLike[str], *, ttl: Optional[float] = 3600.0) -> None:
        self.directory = Path(directory).expanduser()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl

    @staticmethod
    def make_key(method: str, url: str, params: Optional[Mapping[str, Any]] = None) -> str:
        """A stable cache key for a request.

        Params are sorted so ``?a=1&b=2`` and ``?b=2&a=1`` share an entry.

        Request headers are deliberately *not* part of the key: keeping them out
        means no credential ever reaches a cache filename, and it is why a
        response that varies by header is refused at write time instead.
        """
        canonical = {
            "method": method.upper(),
            "url": url,
            "params": sorted((str(k), str(v)) for k, v in (params or {}).items()),
        }
        blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _path_for(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str, *, ttl: Optional[float] = None) -> Optional[CachedResponse]:
        """Return a cached response, or ``None`` on miss/expiry.

        An expired entry is deleted eagerly so the directory self-cleans. The
        effective TTL is the *smaller* of the local TTL and whatever freshness
        the server declared when the entry was stored.
        """
        path = self._path_for(key)
        try:
            raw = path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Corrupt entry — treat as a miss and remove it.
            self._safe_unlink(path)
            return None

        headers = dict(data.get("headers", {}))
        # Belt and braces: entries with a Vary are refused at write time, so one
        # here was written by hand or by an older version. Serving it could hand
        # back the wrong variant, and we cannot revalidate — drop it.
        vary = _header(headers, "Vary")
        if vary is not None and vary.strip():
            self._safe_unlink(path)
            return None

        effective_ttl = ttl if ttl is not None else self.ttl
        server_ttl = data.get("server_ttl")
        if server_ttl is not None:
            server_ttl = float(server_ttl)
            effective_ttl = (
                server_ttl if effective_ttl is None else min(effective_ttl, server_ttl)
            )

        created_at = float(data.get("created_at", 0.0))
        if effective_ttl is not None and (time.time() - created_at) > effective_ttl:
            self._safe_unlink(path)
            return None

        return CachedResponse(
            status_code=int(data["status_code"]),
            headers=headers,
            content=base64.b64decode(data["content"]),
            url=data.get("url", ""),
            created_at=created_at,
        )

    def set(
        self,
        key: str,
        *,
        status_code: int,
        headers: Mapping[str, str],
        content: bytes,
        url: str,
    ) -> bool:
        """Store a response atomically (write-to-temp then rename).

        Returns ``True`` if the entry was written, ``False`` if the response was
        refused (``no-store`` or a non-empty ``Vary`` — see :func:`is_storable`).
        Only allowlisted headers are persisted; ``headers`` should be the full
        response headers so the directives above can be read from them.
        """
        if is_storable(headers) is not None:
            return False

        payload = {
            "status_code": status_code,
            "headers": filter_response_headers(headers),
            "content": base64.b64encode(content).decode("ascii"),
            "url": url,
            "created_at": time.time(),
            # Upper bound on freshness declared by the server, if any.
            "server_ttl": server_freshness(headers),
        }
        path = self._path_for(key)
        # Atomic write: never leave a half-written entry that a reader could see.
        fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, separators=(",", ":"))
            os.replace(tmp, path)
        except BaseException:
            self._safe_unlink(Path(tmp))
            raise
        return True

    def clear(self) -> int:
        """Delete every cache entry. Returns the number removed."""
        removed = 0
        for path in self.directory.glob("*.json"):
            if self._safe_unlink(path):
                removed += 1
        return removed

    @staticmethod
    def _safe_unlink(path: Path) -> bool:
        try:
            path.unlink()
            return True
        except OSError:
            return False
