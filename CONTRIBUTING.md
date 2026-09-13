# Contributing

One maintainer, best-effort responses, no promised turnaround. Issues and pull requests are read.

## Before you open a pull request

```bash
pip install -e ".[dev]"
pytest
```

Everything lands through a pull request with green CI on every Python version the package
declares. `main` is protected; nothing is pushed to it directly, by anyone. Releases are cut from
tags by the workflow described in [`RELEASING.md`](RELEASING.md), never from a working tree.

## What a good pull request looks like

- One change, with a test that fails without it. A check that has only ever been seen passing has
  not been seen working.
- An entry under `[Unreleased]` in `CHANGELOG.md` when behaviour changes.
- No new promise in the README that the code does not keep; the release workflow greps for the
  ones that were retired.

## Security

Report vulnerabilities privately — see [`SECURITY.md`](SECURITY.md) — not in a public issue.
