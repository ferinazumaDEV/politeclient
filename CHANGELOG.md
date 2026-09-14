# Changelog

Notable changes, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.2] — 2026-09-14

### Deprecated

- **Python below 3.11 is compatibility, not support.** The floor becomes **3.11 in 0.2.0** (3.10 reaches
  end of life in October 2026). Nothing changes within 0.1.x; CI keeps running on every version the
  package still declares.

### Fixed

- **`RetryPolicy.backoff_for` no longer raises `OverflowError` past attempt ~1024.** `2 ** attempt`
  is an exact int in Python and stops fitting a float there, so a client configured with a large
  `max_retries` crashed on the attempt it should simply have slept `max_backoff` for. It now does.
- **`parse_retry_after` follows the `delay-seconds` grammar of RFC 9110 (ASCII digits only).**
  `int()` also accepted `+5`, `-5` and digits from other scripts (`٣`, `１２`); those now return
  `None`, so the computed backoff applies instead. A value with more digits than a float can hold
  raised `OverflowError`; it now means "never", which `compute_delay` clamps to `max_backoff`.
- **`TokenBucket.acquire` rejects zero, negative and NaN token counts with `ValueError`.** A negative
  amount *refilled* the bucket past its capacity (`acquire(-10)` on a bucket of 2 left 12 tokens: a
  limiter you could bypass), zero was a silent no-op, and NaN never returned because it compares
  false with everything.
- **`TokenBucket.acquire` no longer spins when the refill lands a few ULPs short of the request.**
  With a deterministic clock (what the `clock`/`sleep` parameters exist for) the sub-ULP sleep was
  absorbed and the loop never advanced; with the real clock it busy-waited until the monotonic clock
  ticked. A deficit within floating-point noise now counts as satisfied.
- **`RateLimit`, `TokenBucket` and `RetryPolicy` reject non-finite configuration at construction.**
  `rate=inf` overflowed in `capacity`, `rate=nan` slipped past `rate <= 0` and blew up in `math.ceil`,
  `burst=inf` built a bucket that never limits, and `max_backoff=nan` would have slept NaN. Rate-limit
  cases raise `RateLimitConfigError`; retry-policy cases raise `ValueError`.

All five were found by `tests/test_adversarial.py`, a new suite that states the invariants a caller
relies on (a bounded, finite backoff; a bucket whose tokens only go down when acquired; the RFC
grammar) and feeds the code the inputs a real client meets at the edges. Every test in it failed
against the previous code before the fix was written.

## [0.1.1] — 2026-09-13

### Added

- **A release pipeline.** A tag now builds in a clean job, refuses artefacts
  containing a virtualenv or repository metadata, installs the wheel and the
  sdist into separate empty environments and exercises each, attests build
  provenance, publishes, and then installs from PyPI to check that what the
  index serves is what was built. Nothing can be published from a working tree.
- `scripts/smoke.py` — what the release pipeline runs against the *installed*
  package: the version the code reports matches the metadata pip installed,
  every name in `__all__` resolves, the token bucket actually spends a token,
  `parse_retry_after` reads a delay, `dig` walks a nested response, and a
  client constructs without touching the network.
- CI across Python 3.9–3.12 on every push and pull request, running the 92
  offline tests. They spin up local HTTP servers and never leave the machine,
  so CI runs exactly what the author runs.
- This changelog.

### Changed

- `actions/checkout` and `actions/setup-python` on v7; no deprecated-runtime
  warnings.
- CI on Python 3.13 and 3.14 as well; classifiers list every version tested.


## [0.1.0] — 2026-09-05

First tagged release and first publication to PyPI.

### Fixed

- **The disk cache could persist credentials.** Responses were being stored
  with sensitive headers intact. The cache now fails *closed* across every
  route a credential can arrive by — request headers, `Authorization`,
  cookies, `session.auth`, the cookie jar and `~/.netrc` — and drops
  `Set-Cookie`, `Authorization` and `WWW-Authenticate` before anything is
  written.
- `Cache-Control: no-store` and non-empty `Vary` are honoured: those responses
  are not stored at all.
- `DiskCache.clear()` deletes only files matching the shape it writes, so
  anything else in that directory is left alone.
- Expiry semantics, `last_status`, and ownership of logging handlers.

