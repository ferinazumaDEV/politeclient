#!/usr/bin/env python3
"""Fail if a translated document has fallen behind its source.

    python3 scripts/check-translations.py

Two copies of one fact will drift. That is not a risk, it is a certainty, and
every serious defect this repository has had was an instance of it: an archive
that no longer said what `main` said, a README claiming a Python version the
package did not support. A stale translation is the same bug with a wider blast
radius, because a reader in that language has no reason to suspect it.

So each translated file records the commit of the source it was translated from:

    <!-- synced-from: <40-char sha of the source file's last commit> -->

This checks that the source has not moved since. When it has, the build fails
and the only ways out are to update the translation, or to look at the diff,
decide it does not change meaning, and bump the marker deliberately. Both are
decisions. Silence is not one of them.

Exit 0 when every pair is in sync, 1 otherwise.
"""

from __future__ import annotations

import re
import subprocess
import sys

# (translation, source). Add a pair here when a document becomes bilingual.
PAIRS = [("README.es.md", "README.md")]

MARKER = re.compile(r"<!--\s*synced-from:\s*([0-9a-f]{40})\s*-->")


def last_commit(path: str) -> str:
    out = subprocess.run(["git", "log", "-1", "--format=%H", "--", path],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def main() -> int:
    problems = []

    for translation, source in PAIRS:
        try:
            text = open(translation, encoding="utf-8").read()
        except FileNotFoundError:
            problems.append(f"{translation} is listed as a translation of {source} but does not exist")
            continue

        m = MARKER.search(text)
        if not m:
            problems.append(
                f"{translation} carries no 'synced-from' marker. Add "
                f"<!-- synced-from: {last_commit(source) or '<sha>'} --> once it matches {source}.")
            continue

        recorded, current = m.group(1), last_commit(source)
        if not current:
            problems.append(f"{source} has no commit history; cannot check {translation}")
        elif recorded != current:
            problems.append(
                f"{source} has changed since {translation} was translated.\n"
                f"      recorded: {recorded[:12]}\n"
                f"      current:  {current[:12]}\n"
                f"      see:      git diff {recorded[:12]}..{current[:12]} -- {source}\n"
                f"      Then either update {translation}, or confirm the change does not\n"
                f"      alter meaning, and set the marker to {current}.")
        else:
            print(f"ok    {translation} is in sync with {source} ({recorded[:12]})")

    if problems:
        sys.stderr.write(f"\nTranslation check FAILED — {len(problems)} problem(s):\n")
        for p in problems:
            sys.stderr.write(f"  - {p}\n")
        return 1

    print(f"\n{len(PAIRS)} translation pair(s) in sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
