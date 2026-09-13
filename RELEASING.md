# Releasing

How a release of `politeclient` is made, what evidence it leaves behind, how anyone
can check that evidence without trusting this repository, and what to do when
a release stops halfway. Written for the person doing it and for the person
auditing it; the two should not need different documents.

## What triggers a release

A tag `vX.Y.Z` pushed to this repository — nothing else. Pushing a tag runs
[`.github/workflows/release.yml`](.github/workflows/release.yml) end to end.
A manual run of that workflow (`workflow_dispatch`) builds and tests but does
**not** publish: the publish job is conditioned on a tag push.

Tags are never moved. A tag that turns out to be wrong is left where it is and
a new patch version is released; see *When it stops halfway* below.

## What the workflow does, in order

| Job | What it proves | Evidence it leaves |
|---|---|---|
| `verify` | The tag, `pyproject.toml` and `CHANGELOG.md` agree on the version. | The job log. |
| `test` | The repository's own suite passes on every declared Python. | The job log, one row per version. |
| `build` | A clean job builds the wheel and sdist; the artefacts contain no virtualenv, `.git`, `.env` or site-packages; a bill of materials is produced **of the package as installed from that wheel**, not of the runner. | The `dist` artefact: wheel, sdist, `SHA256SUMS`, `sbom.cyclonedx.json`. |
| `smoke` | The wheel and the sdist each install into a fresh environment and `scripts/smoke.py` passes against the installed package — separately. | The job log. |
| `publish` | Build provenance is attested; the wheel and sdist are uploaded to PyPI through Trusted Publishing (OIDC — no long-lived token exists); a GitHub Release is created with the wheel, sdist, `SHA256SUMS` and `sbom.cyclonedx.json`. | The attestation on GitHub; the PyPI release with PEP 740 provenance; the GitHub Release and its assets. |
| `verify-published` | Installed **from PyPI** into a fresh environment, the package passes the smoke test; every file PyPI serves for this version has the SHA-256 that `build` recorded; every file carries a provenance record. | The job log, one line per file. |

The publish step runs in the `pypi` environment, which only accepts `v*`
tags. The trusted publisher registered on PyPI is bound to this repository,
this workflow file and that environment name.

## How to verify a release yourself

You need nothing from this repository's maintainer. Everything below is
public.

```bash
# 1. The GitHub Release assets match their published checksums.
gh release download vX.Y.Z --repo ferinazumaDEV/politeclient
sha256sum -c SHA256SUMS

# 2. The wheel and sdist were built by this repository's workflow, on GitHub,
#    from the tagged commit -- not on someone's laptop.
gh attestation verify ./*.whl --repo ferinazumaDEV/politeclient
gh attestation verify ./*.tar.gz --repo ferinazumaDEV/politeclient

# 3. PyPI serves those same bytes, and says where they came from.
curl -s https://pypi.org/pypi/politeclient/X.Y.Z/json \
  | jq -r '.urls[] | "\(.filename)  \(.digests.sha256)"'
#    The digests must equal the lines in SHA256SUMS.
#    Provenance lives in PyPI's Integrity API, not in that JSON: its
#    `provenance` key is null for every file on PyPI, attested or not.
curl -s -H 'Accept: application/vnd.pypi.integrity.v1+json' \
  https://pypi.org/integrity/politeclient/X.Y.Z/FILENAME/provenance \
  | jq '[.attestation_bundles[].attestations | length] | add'
#    Once with FILENAME = the wheel, once = the sdist; both must print >= 1.

# 4. What the package depends on, as installed.
jq '.components[] | "\(.name) \(.version)"' sbom.cyclonedx.json
```

If any of those four disagrees, do not trust the release, and please open an
issue saying which one.

## When it stops halfway

The jobs are ordered so that nothing public happens before everything private
has passed. The two failure shapes that matter:

**It failed before `publish`.** Nothing was published. Fix the cause on
`main`, bump the patch version, and push a new tag. Do not delete or move the
failed tag: a tag that was pushed is part of the history, and a re-pushed tag
would make two different builds carry the same name.

**PyPI accepted the upload but the GitHub Release was not created** (the
network, GitHub, a token). PyPI will refuse a second upload of the same
version, so the workflow must **not** be re-run for that tag. Create the
release by hand from the artefact the run already produced — it is the same
bytes PyPI holds:

```bash
gh run download <run-id> --repo ferinazumaDEV/politeclient --name dist --dir dist
gh release create vX.Y.Z --repo ferinazumaDEV/politeclient --title vX.Y.Z \
  --notes "See CHANGELOG.md. Built by run <run-id>; created by hand after the run's release step failed." \
  dist/*.whl dist/*.tar.gz dist/SHA256SUMS dist/sbom.cyclonedx.json
```

Then run the checks in the previous section against it. The note in the
release says what happened; a release that hides its own history is worth
less than one that explains it.

**`verify-published` failed after a successful publish.** Something PyPI
serves differs from what was built, or lacks provenance. That is not a
recoverable state for the version: yank it on PyPI, say why in the CHANGELOG,
and release a new patch version.
