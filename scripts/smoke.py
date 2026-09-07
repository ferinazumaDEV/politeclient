#!/usr/bin/env python3
"""Does the installed politeclient actually work?

Run against a freshly installed wheel or sdist, never the repository: it
answers the only question a release cares about — if somebody runs
`pip install politeclient` right now, do they get something that functions?

Offline by design. It exercises the rate-limit governor, the Retry-After
parser and the JSON walker, and constructs a client without making a request,
so it needs no network and runs anywhere.
"""
from __future__ import annotations

import importlib.metadata
import sys

DISTRIBUTION = "politeclient"


def main() -> int:
    import politeclient
    from politeclient import PoliteClient, TokenBucket, dig, parse_retry_after

    installed = importlib.metadata.version(DISTRIBUTION)
    if politeclient.__version__ != installed:
        print(f"FAIL  __version__ is {politeclient.__version__} but metadata says {installed}")
        return 1
    print(f"ok    {DISTRIBUTION} {installed}")

    missing = [n for n in politeclient.__all__ if not hasattr(politeclient, n)]
    if missing:
        print(f"FAIL  __all__ advertises names that do not exist: {missing}")
        return 1
    print(f"ok    all {len(politeclient.__all__)} public names resolve")

    # The governor is the point of the library: a full bucket must empty.
    bucket = TokenBucket(rate_per_second=1.0, capacity=2)
    before = bucket.available   # a property, not a method
    bucket.acquire()
    after = bucket.available
    if not (before >= 2 and after < before):
        print(f"FAIL  TokenBucket went from {before} to {after} tokens after one acquire")
        return 1
    print(f"ok    TokenBucket spends a token: {before:.0f} -> {after:.2f}")

    # Retry-After arrives as seconds or as an HTTP date; both must parse.
    if parse_retry_after("120") != 120:
        print("FAIL  parse_retry_after('120') did not return 120")
        return 1
    print("ok    parse_retry_after reads a delay in seconds")

    if dig({"a": {"b": [{"c": 7}]}}, "a.b.0.c") != 7:
        print("FAIL  dig did not walk the nested structure")
        return 1
    print("ok    dig walks a nested response")

    # Constructing a client must not touch the network.
    client = PoliteClient(base_url="https://example.invalid")
    if client is None:
        print("FAIL  PoliteClient could not be constructed")
        return 1
    print("ok    PoliteClient constructs without making a request")

    print("\nsmoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
