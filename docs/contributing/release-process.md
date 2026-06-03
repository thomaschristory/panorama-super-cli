# Release process

`psc` is tag-driven. Pushing a `vX.Y.Z` tag on `main` fires `release.yml`, which
publishes to PyPI via **trusted publishing** (no API tokens) and creates a
GitHub Release. The same tag deploys the docs site.

While on `0.x`, minor versions may include breaking changes; from v1.0.0 the
project follows [SemVer](https://semver.org/).

## Checklist

1. `just test` green.
2. `just lint` clean.
3. `just docs-build` (strict) passes.
4. Roll `CHANGELOG.md`: move `[Unreleased]` to `## vX.Y.Z — YYYY-MM-DD`.
5. Bump the version in **both** `pyproject.toml` and `psc/_version.py` (they must
   agree — the release workflow validates the tag against `psc/_version.py`).
6. Commit on a `chore/release-X.Y.Z` branch, open a PR, merge to `main`.
7. Tag the merge commit and push:
   ```console
   git checkout main && git pull
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin vX.Y.Z
   ```
8. Watch `release.yml`: it verifies the tag is on `main`, the version matches,
   builds the wheel + sdist, publishes to PyPI, and cuts the GitHub Release from
   the CHANGELOG section.

## Pre-tag prerequisites (one-time)

- A **PyPI Trusted Publisher** registered for this repo + `release.yml` +
  *no environment*. Adding a workflow `environment:` would change the OIDC
  subject and break the binding.
- GitHub Pages set to **GitHub Actions** as the source (the docs deploy job
  needs it).

## Hot-fixing a tagged release

Never amend a tagged commit. Increment the patch version, cut a fresh release
with the fix, and add a new `CHANGELOG.md` entry.
