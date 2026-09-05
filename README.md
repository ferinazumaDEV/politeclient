# politeclient

![Python](https://img.shields.io/badge/python-3.9%2B-blue) ![License](https://img.shields.io/badge/license-MIT-green)

**A careful, well-behaved HTTP client for Python — every good-citizen behaviour you keep re-writing for each new API, in one small wrapper around `requests`.**

Retries with exponential backoff **and jitter**, `Retry-After` support, a per-host rate-limit governor, an honest default `User-Agent`, an optional on-disk GET cache, cursor & offset pagination, sane timeouts and structured logging — behind a clean, pythonic API.

It is a **building block for developers**, not a scraper. You bring the endpoints; politeclient makes sure your client behaves.

```python
from politeclient import PoliteClient, RateLimit, RetryPolicy

with PoliteClient(
    base_url="https://api.example.com",
    rate_limit=RateLimit(rate=5),           # 5 requests/second, per host
    retry=RetryPolicy(max_retries=4),       # backoff + jitter, honours Retry-After
    cache="~/.cache/politeclient",          # optional disk cache for GETs
) as client:
    users = client.get("/users").json()
    for post in client.paginate_cursor("/posts", items_key="data", cursor_key="paging.next"):
        ...
```

---

## Why

Almost every "quick script that talks to an API" grows the same crufty appendages once it hits production: a retry loop that forgets jitter and stampedes the server, a `time.sleep(1)` that pretends to be rate limiting, a hand-rolled JSON cache with a race condition, and the eternal *"why do I get a 403 in my script but not my browser?"* (spoiler: your `User-Agent` says `python-requests/2.x`).

`politeclient` packages the correct version of each of those, once.

## Features

- **Retries done right** — exponential backoff with **full jitter** (the [AWS-recommended](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/) anti-thundering-herd strategy), only for idempotent methods by default, capped and configurable.
- **`Retry-After` aware** — when a server tells you when to come back (seconds *or* an HTTP date), politeclient listens instead of guessing (the wait is clamped to `max_backoff`, 60 s by default).
- **Per-host rate-limit governor** — a thread-safe token bucket per host, so a slow, rate-limited API never starves a fast one. Supports sustained rate + bursts.
- **Honest default `User-Agent`** — sends an identifying UA instead of `python-requests/x.y.z`, the single most common cause of surprise `403`s. Override it with one kwarg.
- **Optional disk cache for GETs** — content-addressed, TTL'd, atomic writes. Iterate on a scraper without hammering the API every run. **Authenticated requests are not cached** unless you ask for it explicitly. It stores an allowlist of response headers only, skips `no-store` and `Vary` responses, and lets the server's `max-age`/`Expires` shorten the TTL — see [Cache limits](#cache-limits).
- **Pagination helpers** — lazy generators for both **cursor** and **offset/limit** APIs, with dotted-path extraction (`items_key="data.results"`).
- **Sane timeouts** — a request with no timeout can hang forever; politeclient defaults to `(5s connect, 30s read)`.
- **Structured logging** — every request, retry, wait and cache hit as a greppable `key=value` line, or newline-delimited JSON (`POLITECLIENT_LOG=json`).
- **Clean API** — context manager, verb shortcuts, and a `@polite` decorator.
- **Typed & tested** — full type hints, zero dependencies beyond `requests`, 92 tests against a local mock server.

## Install

```bash
pip install politeclient          # from PyPI (once published)
# or, from source:
pip install .
```

Requires Python 3.9+ and `requests`. That's the whole dependency tree.

## Usage

### The client

```python
from politeclient import PoliteClient, RateLimit, RetryPolicy

client = PoliteClient(
    base_url="https://api.github.com",
    rate_limit=RateLimit(rate=10, per=1.0, burst=10),   # 10/s, burst up to 10
    retry=RetryPolicy(max_retries=5, backoff_factor=0.5, max_backoff=30),
    user_agent="my-app/1.0 (+https://my-app.example)",
    timeout=(5, 30),
)
resp = client.get("/repos/psf/requests")
resp.raise_for_status()
print(resp.json()["stargazers_count"])
client.close()
```

`base_url` is joined to the path you pass with `urllib.parse.urljoin`, so the usual RFC 3986 rules apply. A bare host works either way, but a base that carries a path prefix must end with `/` and the paths must be relative:

```python
client = PoliteClient(base_url="https://api.example.com/v1/")
client.get("users")     # -> https://api.example.com/v1/users
client.get("/users")    # -> https://api.example.com/users  (a leading slash resets to the host root)
# base_url="https://api.example.com/v1" (no trailing slash) drops the /v1 segment the same way.
```

An absolute URL passed to a verb is used as-is.

Or as a context manager (recommended):

```python
with PoliteClient(rate_limit=RateLimit(rate=5)) as client:
    data = client.get("https://httpbin.org/json").json()
```

### The `@polite` decorator

When you'd rather not pass a client around, `@polite` builds one and injects it:

```python
from politeclient import polite, RateLimit

@polite(rate_limit=RateLimit(rate=5), base_url="https://api.example.com")
def fetch_user(client, user_id):
    return client.get(f"/users/{user_id}").json()

fetch_user(42)              # the shared, rate-limited client is injected
fetch_user.client.close()  # ...and reachable when you're done
```

### Pagination

```python
# Offset / limit — stops automatically on the last (short) page:
for row in client.paginate_offset("/records", items_key="data", limit=100):
    process(row)

# Cursor — follows the "next" token until it runs out:
for row in client.paginate_cursor("/feed", items_key="items", cursor_key="paging.next"):
    process(row)
```

Both are lazy generators, so `itertools.islice(...)` or an early `break` only fetches the pages you actually consume.

### Caching

```python
with PoliteClient(cache="~/.cache/myscraper", cache_ttl=3600) as client:
    a = client.get("/expensive")          # network
    b = client.get("/expensive")          # served from disk
    assert b.from_cache is True
    fresh = client.get("/expensive", use_cache=False)   # force network
```

### Cache limits

**Requests that carry credentials are not cached.** The cache key is built from
method, URL and params only — never from headers, so no credential ever reaches a
filename. The consequence is that two callers with different tokens would produce
the *same* key, and one could be served the other's personalised response. Rather
than put credentials in the key, `politeclient` skips the cache entirely when the
request carries credentials by any of the routes `requests` accepts: an explicit
`Authorization`, `Cookie`, `Proxy-Authorization` or `WWW-Authenticate` header on
the request or the session, a per-request `auth=` or `cookies=`, `session.auth`,
a non-empty session cookie jar (any cookie, for any domain — for example one set
by an earlier login response), and `~/.netrc` when the session's `trust_env` is
on (the `requests` default). The check is deliberately conservative: when in
doubt, nothing is written.

If you know a given authenticated response is identical for every caller, opt in
per request:

```python
client.request("GET", url, headers={"Authorization": token}, use_cache=True)
```

That is a deliberate statement, not a default. Servers *should* mark unshareable
responses `no-store` or `Vary`, and those are honoured too — but many do not, so
the safe default does not depend on the server getting it right.


The cache is **off by default**; it only exists if you pass `cache=`. When you do turn it on, this is exactly what it is — a small private cache for iterating on a script, not an HTTP caching implementation:

- **The key is `method + URL + sorted(params)`, and nothing else.** No request headers, no cookies, no credentials go into it — which is also why a response that declares `Vary` is not cached at all: the key cannot tell one variant from another, so serving it back would risk handing you the wrong one.
- **Entries are plain, unencrypted JSON files** (the body is base64, which is encoding, not encryption) in the directory *you* choose. You own that path and its permissions — see [SECURITY.md](SECURITY.md).
- **Only these response headers are persisted:** `Content-Type`, `Content-Encoding`, `ETag`, `Last-Modified`, `Date`, `Vary`. Everything else — `Set-Cookie`, `Authorization`, `WWW-Authenticate` and any header nobody thought about — is dropped on the way to disk.
- **`Cache-Control: no-store` is honoured on write**, and `no-cache` is treated as "never fresh", because this cache cannot revalidate.
- **The server's `max-age` / `Expires` is an upper bound on your TTL.** The shorter of the two wins; `cache_ttl` can only make an entry expire *sooner* than the server said, never later.
- **It is not RFC 9111.** No revalidation with `ETag`/`Last-Modified`, no `stale-while-revalidate`, no shared-cache semantics. If you need those, put a real caching proxy in front.

## Demo

`examples/demo.py` is fully self-contained — it starts a local server that misbehaves on purpose (429s with `Retry-After`, offset pagination, a cacheable endpoint) and drives it. No network, no keys:

```
$ python examples/demo.py

=== Retries + Retry-After ===
final status: 200  body: {'ok': True}  (server was hit 3x)

=== Rate-limit governor (5 req/s, burst 5) ===
8 requests took 0.60s (first 5 instant, then throttled to ~0.2s each)

=== Disk cache for GETs ===
call 1 from_cache=False  body={'served_at_hit': 1}
call 2 from_cache=True   body={'served_at_hit': 1}  (server hit 1x total)

=== Offset pagination ===
collected 23 items across pages: [0, 1, 2, ..., 22]
```

The structured log lines it emits along the way (here in `key=value` mode):

```
[warning] event=retry   method=GET url=.../rate-limited status=429 attempt=1 retry_after=1 sleep=1.0
[warning] event=retry   method=GET url=.../rate-limited status=429 attempt=2 retry_after=1 sleep=1.0
[info]    event=request method=GET url=.../rate-limited status=200 attempt=3 elapsed_ms=2.0
```

…or as JSON with `POLITECLIENT_LOG=json`:

```json
{"level":"warning","event":"retry","method":"GET","url":".../x","status":429,"attempt":1,"retry_after":"1","sleep":1.0}
{"level":"info","event":"request","method":"GET","url":".../x","status":200,"attempt":2,"elapsed_ms":1.9}
```

## How it works

Each `request()` runs through the same pipeline:

1. **Cache lookup** (GET only) — a content-addressed key over `method + url + sorted(params)`; a fresh hit short-circuits the whole thing and returns a response with `from_cache is True`. "Fresh" is the shorter of your TTL and the freshness the server declared.
2. **Rate-limit gate** — the request acquires a token from the host's bucket, blocking just long enough if the bucket is empty. Buckets refill lazily (no background threads): each acquire computes how many tokens *would* have dripped in since the last call.
3. **Send + evaluate** — on a retryable status (`429`, `5xx`) or a transient transport error (connection reset, timeout), it computes the next delay. `Retry-After` wins when present and valid (clamped to `max_backoff`, 60 s by default — raise it if your API asks for longer waits); otherwise it's `backoff_factor · 2ⁿ` capped at `max_backoff`, then **full jitter** picks a random point in `[0, that]`.
4. **Retry or return** — non-idempotent methods (`POST`) aren't retried by default, because retrying them can duplicate work. Once the budget is spent, an HTTP failure is returned as-is (so you can `raise_for_status()`), while a transport failure raises `RetryBudgetExceeded`.
5. **Store** — a successful GET is written to the cache atomically (temp file + `os.replace`), keeping only allowlisted headers and skipping responses marked `no-store` or `Vary` ([Cache limits](#cache-limits)).

The token bucket, retry policy, cache and pagination are each independent, importable pieces (`TokenBucket`, `RetryPolicy`, `DiskCache`, `paginate_cursor`), so you can reuse one without buying into the whole client.

## Development

```bash
pip install -e ".[dev]"
pytest                     # 92 tests, all offline
python examples/demo.py    # the tour above
```

The test suite spins up a small programmable HTTP server (`tests/conftest.py`) and scripts exact failure sequences — three 429s then a 200, a 500 storm, a `Retry-After` header, paginated datasets — so retries, backoff, rate limiting and caching are verified against real sockets, deterministically and without touching the network.

## Part of a family of small tools

politeclient is one of a family of small, focused building blocks I maintain for Python developers. Its good-citizen HTTP behaviour — honest `User-Agent`s, backoff and per-host rate-limiting — is also the baseline hygiene expected of well-behaved crawlers and AI bots, which is where it brushes lightly against technical GEO (generative engine optimization).

- [The GEO Handbook](https://github.com/ferinazumaDEV/generative-engine-optimization-handbook) — the open reference on getting content cited by AI answer engines (ChatGPT, Perplexity, Google AI Overviews, Gemini, Copilot).
- [webhook-replay](https://github.com/ferinazumaDEV/webhook-replay) — capture a webhook once, then replay it at your local app as many times as you need; the other half of the "HTTP that behaves" toolkit.
- [typedout](https://github.com/ferinazumaDEV/typedout) — reliable structured output from any LLM: schema-validated JSON with tolerant repair and retries.
- [scaffld](https://github.com/ferinazumaDEV/scaffld) — scaffold fully-wired Python projects (tests, CI, pre-commit, license) from templates, with a TUI.
- Hub & writing: [zentimes.es](https://zentimes.es).

By [ferinazumaDEV](https://github.com/ferinazumaDEV).

## License

MIT — see [LICENSE](LICENSE).

---

*Built by Fernando Aporta Franco ([@ferinazumaDEV](https://github.com/ferinazumaDEV)).*
