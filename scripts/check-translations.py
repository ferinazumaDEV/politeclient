#!/usr/bin/env python3
"""Fail if a translated document has fallen behind its source.

    python3 scripts/check-translations.py

Two copies of one fact will drift. That is not a risk, it is a certainty, and
every serious defect this repository has had was an instance of it: an archive
that no longer said what `main` said, a README claiming a Python version the
package did not support. A stale translation is the same bug with a wider blast
radius, because a reader in that language has no reason to suspect it.

So each translated file records the *content* of the source it was translated
from, as git's hash of that file's bytes:

    <!-- synced-from: <40-char blob sha, i.e. `git hash-object README.md`> -->

Why the content and not the commit. The first version of this check recorded
the source's last *commit*, and it broke the first time a pull request was
squash-merged: squashing rewrites history, so the commit the marker pointed at
no longer existed on the target branch and the check failed on a translation
nobody had touched. A blob hash has no such problem -- it survives squash,
rebase and cherry-pick, and changes when, and only when, the bytes change.
Which is the question being asked.

When the source has moved, the build fails and the only ways out are to update
the translation, or to look at the diff, decide it does not change meaning, and
bump the marker deliberately. Both are decisions. Silence is not one of them.

Exit 0 when every pair is in sync, 1 otherwise.
"""

from __future__ import annotations

import re
import subprocess
import sys

# (translation, source). Add a pair here when a document becomes bilingual.
PAIRS = [("README.es.md", "README.md")]

MARKER = re.compile(r"<!--\s*synced-from:\s*([0-9a-f]{40})\s*-->")


def blob_sha(path: str) -> str:
    """git's hash of the file as it is on disk, independent of history."""
    out = subprocess.run(["git", "hash-object", path],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def object_exists(sha: str) -> bool:
    return subprocess.run(["git", "cat-file", "-e", sha],
                          capture_output=True).returncode == 0


def main() -> int:
    problems = []

    for translation, source in PAIRS:
        try:
            text = open(translation, encoding="utf-8").read()
        except FileNotFoundError:
            problems.append(f"{translation} is listed as a translation of {source} but does not exist")
            continue

        try:
            current = blob_sha(source)
        except subprocess.CalledProcessError:
            problems.append(f"{source} does not exist; cannot check {translation}")
            continue

        m = MARKER.search(text)
        if not m:
            problems.append(
                f"{translation} carries no 'synced-from' marker. Add "
                f"<!-- synced-from: {current} --> once it matches {source}.")
            continue

        recorded = m.group(1)
        if recorded == current:
            print(f"ok    {translation} is in sync with {source} ({recorded[:12]})")
            continue

        # The recorded blob is usually still reachable, so git can show the exact
        # diff. When it is not -- a shallow clone, or a history that was pruned --
        # say so instead of printing a command that will fail.
        if object_exists(recorded):
            how = f"git diff {recorded[:12]} {current[:12]}"
        else:
            how = (f"git log -p -- {source}    "
                   f"(blob {recorded[:12]} is not in this clone; "
                   f"a full-depth fetch would let git diff it directly)")

        problems.append(
            f"{source} has changed since {translation} was translated.\n"
            f"      recorded: {recorded[:12]}\n"
            f"      current:  {current[:12]}\n"
            f"      see:      {how}\n"
            f"      Then either update {translation}, or confirm the change does not\n"
            f"      alter meaning, and set the marker to {current}.")

    if problems:
        sys.stderr.write(f"\nTranslation check FAILED — {len(problems)} problem(s):\n")
        for p in problems:
            sys.stderr.write(f"  - {p}\n")
        return 1

    print(f"\n{len(PAIRS)} translation pair(s) in sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
