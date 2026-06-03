# Branching and merging

Trunk-based development with short-lived feature branches. There is one
long-lived branch — `main` — and it is always releasable.

## Branches

- **`main`** — the integration branch; every accepted change lands here, and
  each commit is a candidate for the next release.
- **Feature branches** — short-lived, named for the change, deleted on merge.
- **Tags** (`vX.Y.Z`) — the release artifact; a tag points at a commit on `main`.

## Naming

`<type>/<short-slug>` matching Conventional Commit types:

- `feat/<slug>`, `fix/<slug>`, `docs/<slug>`, `refactor/<slug>`,
  `chore/<slug>`, `ci/<slug>`, `test/<slug>`.

## Lifecycle

1. Branch from latest `main`.
2. Commit using Conventional Commits.
3. Push, open a PR against `main`; CI runs (`lint`, `test`).
4. Once green (and reviewed), **squash-merge**. One commit per PR keeps `main`
   easy to scan and revert.
5. Delete the branch.

## Branch protection on `main`

- No direct pushes — everything goes through a PR.
- Required status checks: `lint` and `test (ubuntu-latest, 3.12)`.
- Force pushes and branch deletion blocked.

## Releases are tags, not branches

A release is a tagged commit on `main`. Release prep (CHANGELOG roll + version
bumps) happens on a `chore/release-X.Y.Z` branch, merges to `main`, and only then
is `vX.Y.Z` tagged. See [Release process](release-process.md).
