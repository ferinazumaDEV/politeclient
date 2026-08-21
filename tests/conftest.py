"""Shared test fixtures, including a small programmable HTTP mock server.

The mock server runs on a background thread on an OS-assigned port. Route
handlers are plain callables that receive a :class:`Ctx` (query params, the
per-path hit count, and a shared ``state`` dict) and return
``(status, headers, body)``. This lets each test script exactly the failure
sequence it wants — three 429s then a 200, a 500 storm, a ``Retry-After``
header, an offset- or cursor-paginated dataset — with zero external services.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional, Tuple, Union
from urllib.parse import parse_qs, urlsplit

import pytest

Body = Union[str, bytes, dict, list]
RouteResult = Tuple[int, Dict[str, str], Body]


@dataclass
class Ctx:
    method: str
    path: str
    query: Dict[str, str]
    count: int  # 1-indexed hit count for this path (this request included)
    state: Dict[str, Any]
    headers: Dict[str, str]


RouteFn = Callable[[Ctx], RouteResult]


@dataclass
class MockServer:
    routes: Dict[str, RouteFn] = field(default_factory=dict)
    hits: Dict[str, int] = field(default_factory=dict)
    state: Dict[str, Any] = field(default_factory=dict)
    _server: Optional[ThreadingHTTPServer] = None
    _thread: Optional[threading.Thread] = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def route(self, path: str, fn: RouteFn) -> None:
        self.routes[path] = fn

    @property
    def port(self) -> int:
        assert self._server is not None
        return self._server.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def hit_count(self, path: str) -> int:
        with self._lock:
            return self.hits.get(path, 0)

    def _next_count(self, path: str) -> int:
        with self._lock:
            self.hits[path] = self.hits.get(path, 0) + 1
            return self.hits[path]

    def start(self) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # silence stderr spam
                pass

            def _dispatch(self, method: str) -> None:
                parsed = urlsplit(self.path)
                path = parsed.path
                query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                fn = outer.routes.get(path)
                if fn is None:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b"not found")
                    return
                count = outer._next_count(path)
                ctx = Ctx(
                    method=method,
                    path=path,
                    query=query,
                    count=count,
                    state=outer.state,
                    headers={k.lower(): v for k, v in self.headers.items()},
                )
                status, headers, body = fn(ctx)
                if isinstance(body, (dict, list)):
                    payload = json.dumps(body).encode("utf-8")
                    headers.setdefault("Content-Type", "application/json")
                elif isinstance(body, str):
                    payload = body.encode("utf-8")
                else:
                    payload = body
                self.send_response(status)
                headers.setdefault("Content-Length", str(len(payload)))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                if method != "HEAD":
                    self.wfile.write(payload)

            def do_GET(self) -> None:
                self._dispatch("GET")

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                if length:
                    self.rfile.read(length)
                self._dispatch("POST")

            def do_HEAD(self) -> None:
                self._dispatch("HEAD")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


@pytest.fixture
def server() -> MockServer:
    srv = MockServer()
    srv.start()
    try:
        yield srv
    finally:
        srv.stop()
