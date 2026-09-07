# Changelog

Notable changes, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

