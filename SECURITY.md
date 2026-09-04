# Security policy

## Supported versions

politeclient has no published release yet: `0.1.0` is the version declared in `pyproject.toml`, and the README notes that PyPI is still pending. Fixes land on `main`, and there is no long-term support branch.

## Reporting a problem

Please report privately first.

1. Preferred: GitHub's private vulnerability reporting on this repository — **Security → Report a vulnerability** ([direct link](https://github.com/ferinazumaDEV/politeclient/security/advisories/new)).
2. If that form is not available to you, [open an issue](https://github.com/ferinazumaDEV/politeclient/issues) saying only that you have a security report and how to reach you. **Do not put exploit details, tokens, cookies or raw logs in a public issue** — a private channel will be arranged from there.

Expect a first reply within a week. This is a small project maintained in spare time, so please be patient rather than surprised; there is no bounty programme.

When you report, the most useful things to include are the politeclient and Python versions, a minimal reproduction, and what an attacker gains.

## What politeclient writes to disk

**Nothing, unless you enable the cache.** `PoliteClient(cache=...)` is opt-in and off by default.

When it is enabled:

- Entries are one **plain JSON file per request** in the directory you pass. They are *not* encrypted; the response body is base64, which is an encoding, not a protection.
- Each file holds the status code, the response body, the final URL, the timestamp, the freshness the server declared, and only these response headers: `Content-Type`, `Content-Encoding`, `ETag`, `Last-Modified`, `Date`, `Vary`. Every other header — including `Set-Cookie`, `Authorization` and `WWW-Authenticate` — is dropped before anything is written.
- The cache key is a SHA-256 of `method + URL + sorted(params)`. Request headers and cookies are never part of it, so no credential ends up in a filename.
- Responses marked `Cache-Control: no-store`, and responses carrying a non-empty `Vary`, are not stored at all.
- File names are hashes, but the **directory listing still reveals how many entries exist**, and any body you fetch is readable by anyone who can read the directory.

**You choose the directory, so you own its permissions.** politeclient creates it with your process's default umask and does not change it. If the responses you cache are sensitive, put the cache somewhere only your user can read (for example `chmod 700` on the directory), or leave the cache off. `DiskCache.clear()` deletes every entry when you are done — only files named `<sha256>.json`, the shape every entry has, so anything else you keep in that directory is left alone.

## Other things worth knowing

- **Logs.** Structured log lines include the request method, the URL you passed and the status code. Query parameters supplied via `params=` are not logged, but a credential embedded directly in a URL string would be.
- **Redirects.** politeclient uses `requests`' default redirect handling; a redirect to another host is followed, and the cache stores the final URL.
- **TLS verification** is `requests`' default (on). politeclient never disables it for you.
- **No telemetry.** It makes no requests you did not ask for, and politeclient itself reads no configuration beyond the `POLITECLIENT_LOG` environment variable. The underlying `requests` session, however, honours `~/.netrc` (or the file named by `NETRC`) and the proxy and CA-bundle environment variables (`HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`) unless you pass a session with `trust_env=False`. Credentials that `requests` picks up from `~/.netrc` count as credentials for the cache rule above: such responses are not cached.

## Out of scope

Reports that a caller can hurt themselves — passing a cache directory that is world-readable, disabling TLS verification through `requests` themselves, or feeding the client a hostile URL on purpose — are documentation issues rather than vulnerabilities, but they are still welcome as issues.
