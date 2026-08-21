"""A self-contained tour of politeclient.

Run it directly — it starts a tiny local HTTP server that misbehaves on purpose
(429s with ``Retry-After``, a 500 storm, offset pagination) and then drives it
with a :class:`PoliteClient`, narrating what happens.

    python examples/demo.py

No network, no API keys, no external services.
"""

from __future__ import annotations

import logging
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from politeclient import PoliteClient, RateLimit, RetryPolicy
from politeclient.logging_utils import get_logger

HITS: dict[str, int] = {}
DATASET = list(range(23))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep the server quiet
        pass

    def _send(self, status, body=b"", headers=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        HITS[path] = HITS.get(path, 0) + 1
        n = HITS[path]

        if path == "/rate-limited":
            # Two 429s (with Retry-After) then success.
            if n <= 2:
                self._send(429, '{"error":"slow down"}', {"Retry-After": "1"})
            else:
                self._send(200, '{"ok":true}', {"Content-Type": "application/json"})
        elif path == "/list":
            off = int(query.get("offset", 0))
            lim = int(query.get("limit", 10))
            import json

            page = DATASET[off:off + lim]
            self._send(200, json.dumps({"items": page}), {"Content-Type": "application/json"})
        elif path == "/cached":
            import json

            self._send(200, json.dumps({"served_at_hit": n}),
                       {"Content-Type": "application/json"})
        else:
            self._send(404, "nope")


def start_server() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def banner(title: str) -> None:
    print(f"\n\033[1m=== {title} ===\033[0m")


def main() -> None:
    # Make the structured logging visible so you can watch the retries happen.
    get_logger().setLevel(logging.INFO)
    logging.getLogger("politeclient").setLevel(logging.INFO)

    server, base = start_server()
    try:
        # 1) Retries with Retry-After -----------------------------------------
        banner("Retries + Retry-After")
        with PoliteClient(base_url=base, retry=RetryPolicy(max_retries=5)) as client:
            resp = client.get("/rate-limited")
            print(f"final status: {resp.status_code}  body: {resp.json()}  "
                  f"(server was hit {HITS['/rate-limited']}x)")

        # 2) Per-host rate-limit governor -------------------------------------
        banner("Rate-limit governor (5 req/s, burst 5)")
        with PoliteClient(base_url=base, rate_limit=RateLimit(rate=5, burst=5)) as client:
            started = time.monotonic()
            for _ in range(8):
                client.get("/list", params={"limit": 10, "offset": 0})
            elapsed = time.monotonic() - started
            print(f"8 requests took {elapsed:.2f}s "
                  f"(first 5 instant, then throttled to ~0.2s each)")

        # 3) On-disk GET cache -------------------------------------------------
        banner("Disk cache for GETs")
        with tempfile.TemporaryDirectory() as cache_dir:
            with PoliteClient(base_url=base, cache=cache_dir) as client:
                a = client.get("/cached")
                b = client.get("/cached")
                print(f"call 1 from_cache={a.from_cache}  body={a.json()}")
                print(f"call 2 from_cache={b.from_cache}  body={b.json()}  "
                      f"(server hit {HITS['/cached']}x total)")

        # 4) Pagination --------------------------------------------------------
        banner("Offset pagination")
        with PoliteClient(base_url=base) as client:
            items = list(client.paginate_offset("/list", items_key="items", limit=10))
            print(f"collected {len(items)} items across pages: {items}")

    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
