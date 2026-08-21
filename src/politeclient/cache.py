"""Optional on-disk cache for GET responses.

A dead-simple, dependency-free file cache keyed by request identity. It only
ever stores successful, cacheable GET responses and honours a TTL. This is *not*
a full HTTP caching layer (no ETag/Last-Modified revalidation) — it is the "I'm
iterating on a scraper and don't want to hammer the API every run" cache, which
is exactly the cache people hand-roll badly.
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
from typing import Any, Dict, Mapping, Optional


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

    Args:
        directory: Where to store entries (created if missing).
        ttl: Default time-to-live in seconds. ``None`` means never expire.
    """

    def __init__(self, directory: str | os.PathLike[str], *, ttl: Optional[float] = 3600.0) -> None:
        self.directory = Path(directory).expanduser()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl

    @staticmethod
    def make_key(method: str, url: str, params: Optional[Mapping[str, Any]] = None) -> str:
        """A stable cache key for a request.

        Params are sorted so ``?a=1&b=2`` and ``?b=2&a=1`` share an entry.
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

        An expired entry is deleted eagerly so the directory self-cleans.
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

        effective_ttl = ttl if ttl is not None else self.ttl
        created_at = float(data.get("created_at", 0.0))
        if effective_ttl is not None and (time.time() - created_at) > effective_ttl:
            self._safe_unlink(path)
            return None

        return CachedResponse(
            status_code=int(data["status_code"]),
            headers=dict(data.get("headers", {})),
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
    ) -> None:
        """Store a response atomically (write-to-temp then rename)."""
        payload = {
            "status_code": status_code,
            "headers": dict(headers),
            "content": base64.b64encode(content).decode("ascii"),
            "url": url,
            "created_at": time.time(),
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
