"""The :class:`PoliteClient` — a careful, well-behaved HTTP client.

It wraps a :class:`requests.Session` and adds the things people forget:

* retries with exponential backoff **+ jitter**, honouring ``Retry-After``;
* a per-host token-bucket rate-limit governor;
* sane default headers (including a real ``User-Agent`` — see the note below);
* an optional on-disk cache for GET responses;
* sensible default timeouts (a request with *no* timeout can hang forever);
* structured logging of every request, retry, wait and cache hit;
* cursor and offset pagination helpers.

The default ``User-Agent`` note
-------------------------------
``requests`` sends ``python-requests/x.y.z`` by default, and a surprising number
of servers reject exactly that string with **403 Forbidden**. politeclient sends
an honest, identifying UA instead. If you still get a 403, pass a browser-like
``user_agent=...`` — it is the single most common fix for "works in my browser,
403 in my script".
"""

from __future__ import annotations

import random
import threading
import time
from types import TracebackType
from typing import Any, Callable, Dict, Iterator, Mapping, Optional, Tuple, Type, Union
from urllib.parse import urljoin, urlsplit

import requests
from requests.structures import CaseInsensitiveDict
from requests.utils import get_netrc_auth

from . import __version__
from .cache import carries_credentials, CachedResponse, DiskCache
from .exceptions import RetryBudgetExceeded
from .logging_utils import get_logger, log_event
from .pagination import paginate_cursor, paginate_offset
from .ratelimit import RateLimit, TokenBucket
from .retry import RetryPolicy

import logging

Timeout = Union[float, Tuple[float, float]]

DEFAULT_USER_AGENT = (
    f"politeclient/{__version__} (+https://github.com/ferinazumaDEV/politeclient)"
)

# Transport-level failures that are worth retrying (connection reset, timeout…).
_RETRYABLE_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


class PoliteClient:
    """A polite, resilient HTTP client.

    Args:
        base_url: Optional base joined to the paths passed to the verbs with
            :func:`urllib.parse.urljoin`, so RFC 3986 rules apply. A bare host
            works either way, but a base that carries a path prefix must end
            with ``/`` and the paths must be relative:
            ``base_url="https://api.example.com/v1/"`` + ``get("users")``
            requests ``/v1/users``, whereas a leading slash (``"/users"``) or a
            base without the trailing slash resets to the host root and
            silently drops ``/v1``. An absolute URL is used as-is.
        rate_limit: A :class:`RateLimit` applied *per host*. ``None`` disables
            rate limiting.
        retry: A :class:`RetryPolicy`. Defaults to a sensible policy.
        cache: A cache directory (str/Path) or a :class:`DiskCache`. ``None``
            disables caching (the default). Only successful GETs are cached, and
            never one marked ``no-store`` or carrying a ``Vary`` — see
            :mod:`politeclient.cache` for what is stored and what is refused.
        cache_ttl: TTL in seconds when ``cache`` is given as a path. The server's
            own ``max-age``/``Expires`` can only shorten it.
        user_agent: Overrides the default identifying User-Agent.
        headers: Extra default headers merged into every request.
        timeout: Default ``(connect, read)`` timeout, or a single float.
        log_format: ``"kv"`` (default) or ``"json"`` structured logging.

    Example:
        >>> with PoliteClient(
        ...     base_url="https://api.example.com",
        ...     rate_limit=RateLimit(rate=5),
        ...     retry=RetryPolicy(max_retries=4),
        ...     cache="~/.cache/politeclient",
        ... ) as client:
        ...     data = client.get("/users").json()
    """

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        rate_limit: Optional[RateLimit] = None,
        retry: Optional[RetryPolicy] = None,
        cache: Union[None, str, DiskCache] = None,
        cache_ttl: Optional[float] = 3600.0,
        user_agent: Optional[str] = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Timeout = (5.0, 30.0),
        log_format: Optional[str] = None,
        session: Optional[requests.Session] = None,
        # Injection points for tests — real code rarely touches these.
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: Optional[random.Random] = None,
    ) -> None:
        self.base_url = base_url
        self.rate_limit = rate_limit
        self.retry = retry or RetryPolicy()
        self.timeout = timeout
        self._sleep = sleep
        self._clock = clock
        self._rng = rng or random.Random()
        self._logger = get_logger(log_format)

        if isinstance(cache, DiskCache):
            self.cache: Optional[DiskCache] = cache
        elif cache is not None:
            self.cache = DiskCache(cache, ttl=cache_ttl)
        else:
            self.cache = None

        self._session = session or requests.Session()
        default_headers = {
            "User-Agent": user_agent or DEFAULT_USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }
        if headers:
            default_headers.update(headers)
        self._session.headers.update(default_headers)

        self._buckets: Dict[str, TokenBucket] = {}
        self._buckets_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Context-manager plumbing
    # ------------------------------------------------------------------ #
    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close()

    def close(self) -> None:
        self._session.close()

    # ------------------------------------------------------------------ #
    # Rate limiting
    # ------------------------------------------------------------------ #
    def _bucket_for(self, host: str) -> Optional[TokenBucket]:
        if self.rate_limit is None:
            return None
        bucket = self._buckets.get(host)
        if bucket is None:
            with self._buckets_lock:
                bucket = self._buckets.get(host)
                if bucket is None:
                    bucket = TokenBucket.from_config(
                        self.rate_limit, sleep=self._sleep, clock=self._clock
                    )
                    self._buckets[host] = bucket
        return bucket

    # ------------------------------------------------------------------ #
    # Core request path
    # ------------------------------------------------------------------ #
    def request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        use_cache: Optional[bool] = None,
        retry: Optional[RetryPolicy] = None,
        **kwargs: Any,
    ) -> requests.Response:
        """Perform a request with rate limiting, retries and caching.

        ``**kwargs`` are forwarded to ``requests`` (``json=``, ``data=``,
        ``headers=`` …). Returns a :class:`requests.Response`; on a cache hit the
        response carries ``response.from_cache is True``.
        """
        method = method.upper()
        full_url = urljoin(self.base_url, url) if self.base_url else url
        retry_policy = retry or self.retry
        kwargs.setdefault("timeout", self.timeout)

        # A request that carries credentials is not shareable: the cache key is
        # method + URL + params, so two callers with different tokens collide on
        # one entry. Skip the cache unless the caller opts in explicitly with
        # ``use_cache=True``, which is a deliberate "I know this response is the
        # same for everyone" statement.
        #
        # Credentials reach ``requests`` by several routes, not only a literal
        # ``Authorization``/``Cookie`` header: a per-request ``auth=`` or
        # ``cookies=``, ``session.auth``, a cookie jar filled by an earlier
        # ``Set-Cookie`` (a login flow), or ``~/.netrc`` — which ``requests``
        # applies on its own whenever ``trust_env`` is on. Every route is checked
        # and the check fails closed: *any* cookie in the jar, for any domain,
        # disables caching by default. The computation only runs when it can
        # change the outcome, so ``~/.netrc`` is not parsed for a POST or when
        # the cache is off.
        _authenticated = False
        if self.cache is not None and method == "GET" and use_cache is None:
            _authenticated = (
                carries_credentials(kwargs.get("headers"))
                or carries_credentials(self._session.headers)
                or kwargs.get("auth") is not None
                or self._session.auth is not None
                or bool(kwargs.get("cookies"))
                or len(self._session.cookies) > 0
                or bool(self._session.trust_env and get_netrc_auth(full_url))
            )
        cache_enabled = (
            self.cache is not None
            and method == "GET"
            and (use_cache if use_cache is not None else not _authenticated)
        )
        cache_key = None
        if cache_enabled:
            assert self.cache is not None
            cache_key = DiskCache.make_key(method, full_url, params)
            hit = self.cache.get(cache_key)
            if hit is not None:
                log_event(
                    self._logger,
                    logging.DEBUG,
                    "cache_hit",
                    method=method,
                    url=full_url,
                    age=round(hit.age, 1),
                )
                return self._response_from_cache(hit)

        host = urlsplit(full_url).netloc
        bucket = self._bucket_for(host)

        attempt = 0
        last_exception: Optional[BaseException] = None
        last_status: Optional[int] = None
        while True:
            if bucket is not None:
                waited = bucket.acquire()
                if waited > 0:
                    log_event(
                        self._logger,
                        logging.DEBUG,
                        "rate_limited",
                        host=host,
                        waited=round(waited, 3),
                    )

            started = time.monotonic()
            try:
                response = self._session.request(
                    method, full_url, params=params, **kwargs
                )
            except _RETRYABLE_EXCEPTIONS as exc:
                last_exception = exc
                if attempt < retry_policy.max_retries and method in retry_policy.retry_methods:
                    delay = retry_policy.backoff_for(attempt, rng=self._rng)
                    log_event(
                        self._logger,
                        logging.WARNING,
                        "retry",
                        method=method,
                        url=full_url,
                        attempt=attempt + 1,
                        reason=type(exc).__name__,
                        sleep=round(delay, 3),
                    )
                    self._sleep(delay)
                    attempt += 1
                    continue
                raise RetryBudgetExceeded(
                    f"{method} {full_url} failed after {attempt + 1} attempt(s): {exc}",
                    attempts=attempt + 1,
                    last_status=last_status,
                    last_exception=exc,
                ) from exc

            elapsed_ms = round((time.monotonic() - started) * 1000, 1)
            last_status = response.status_code

            if retry_policy.should_retry(method, response.status_code, attempt):
                retry_after = response.headers.get("Retry-After")
                delay = retry_policy.compute_delay(
                    attempt, retry_after=retry_after, rng=self._rng
                )
                log_event(
                    self._logger,
                    logging.WARNING,
                    "retry",
                    method=method,
                    url=full_url,
                    status=response.status_code,
                    attempt=attempt + 1,
                    retry_after=retry_after,
                    sleep=round(delay, 3),
                )
                response.close()
                self._sleep(delay)
                attempt += 1
                continue

            log_event(
                self._logger,
                logging.INFO,
                "request",
                method=method,
                url=full_url,
                status=response.status_code,
                attempt=attempt + 1,
                elapsed_ms=elapsed_ms,
            )

            if cache_enabled and 200 <= response.status_code < 300:
                assert self.cache is not None and cache_key is not None
                # The cache sees the full headers so it can read Cache-Control
                # and Vary, but only persists its own allowlist of them.
                stored = self.cache.set(
                    cache_key,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    content=response.content,
                    url=response.url,
                )
                if not stored:
                    log_event(
                        self._logger,
                        logging.DEBUG,
                        "cache_skip",
                        method=method,
                        url=full_url,
                    )
            setattr(response, "from_cache", False)
            return response

    # ------------------------------------------------------------------ #
    # Verb shortcuts
    # ------------------------------------------------------------------ #
    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def head(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("HEAD", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("DELETE", url, **kwargs)

    # ------------------------------------------------------------------ #
    # Pagination
    # ------------------------------------------------------------------ #
    def paginate_offset(
        self,
        url: str,
        *,
        items_key: Optional[str] = None,
        limit: int = 100,
        limit_param: str = "limit",
        offset_param: str = "offset",
        start_offset: int = 0,
        params: Optional[Mapping[str, Any]] = None,
        max_pages: Optional[int] = None,
        **kwargs: Any,
    ) -> Iterator[Any]:
        """Iterate items from an offset/limit endpoint. See :func:`paginate_offset`."""

        def fetch(page_params: Mapping[str, Any]) -> Any:
            return self.get(url, params=dict(page_params), **kwargs).json()

        return paginate_offset(
            fetch,
            items_key=items_key,
            limit=limit,
            limit_param=limit_param,
            offset_param=offset_param,
            start_offset=start_offset,
            extra_params=params,
            max_pages=max_pages,
        )

    def paginate_cursor(
        self,
        url: str,
        *,
        cursor_key: str,
        items_key: Optional[str] = None,
        cursor_param: str = "cursor",
        start_cursor: Optional[str] = None,
        params: Optional[Mapping[str, Any]] = None,
        max_pages: Optional[int] = None,
        **kwargs: Any,
    ) -> Iterator[Any]:
        """Iterate items from a cursor endpoint. See :func:`paginate_cursor`."""

        def fetch(page_params: Mapping[str, Any]) -> Any:
            return self.get(url, params=dict(page_params), **kwargs).json()

        return paginate_cursor(
            fetch,
            items_key=items_key,
            cursor_key=cursor_key,
            cursor_param=cursor_param,
            start_cursor=start_cursor,
            extra_params=params,
            max_pages=max_pages,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _response_from_cache(cached: CachedResponse) -> requests.Response:
        response = requests.Response()
        response.status_code = cached.status_code
        response._content = cached.content  # type: ignore[attr-defined]
        response.url = cached.url
        response.headers = CaseInsensitiveDict(cached.headers)
        # Derive the encoding from the stored headers exactly as requests would;
        # fall back to UTF-8 when the Content-Type carries no charset.
        response.encoding = (
            requests.utils.get_encoding_from_headers(response.headers) or "utf-8"
        )
        setattr(response, "from_cache", True)
        return response
