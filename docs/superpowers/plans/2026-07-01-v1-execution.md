# psc v1.0.0 overnight execution plan

**Date:** 2026-07-01 (overnight autonomous run). **Goal:** close all 24 open
issues, reach CLI↔workbench parity, ship **v1.0.0**. Full mandate + decisions in
memory `v1-overnight-mandate`. This file is the **resumable progress tracker** —
after any context compaction, re-read it and continue from the first unchecked box.

## Operating loop (per issue — STRICTLY SEQUENTIAL, one PR at a time)

`main` is protected: enforce_admins=true, linear history, strict required checks
(`lint`, `test`). So: only ONE PR open/merging at a time; each starts from fresh
`main`; wait for CI green before merge (can't bypass; admins enforced).

1. `git checkout main && git pull`. New branch `feat|fix|docs/<slug>`.
2. TDD implement (subagent): failing test first → engine (core) → CLI/TUI wiring.
   Core imports nothing from cli/tui. Reuse engines; never reimplement validation.
3. Local gate: `just test` + `just lint` (ruff+format+mypy --strict) all green.
4. Commit (Conventional Commits, `Closes #N`), push, open PR.
5. Adversarial self-review (review agents on the diff). Fix findings, re-gate.
6. `gh pr checks --watch` → green → squash-merge → delete branch.
7. Tick the box here + update memory/task. Next issue.

Anything unsafe to finish → labelled **draft** PR + note, do NOT merge.

## Wave 1 — independent core engines (backend)
- [x] #3  find `--resolve-fqdn` — MERGED (PR #96)
- [x] #4  `dedup merge --group` — MERGED (PR #97)
- [x] #15 `name apply --all` — MERGED (PR #98)
- [x] #76 `move --cascade` — MERGED (PR #99)
- [x] #13 `psc diff` — MERGED (PR #100)
- [x] #14 NDJSON export/import — MERGED (PR #101)

## Wave 2 — audit engine batch (serialize; refs.py/audit.py)
- [x] #9  `refs unused --ignore-disabled` — MERGED (PR #102)
- [x] #11 `audit services-vs-wellknown` — MERGED (PR #103)
- [x] #26 unused-tags over-count — MERGED (PR #104)
- [x] #56 `refs unused` scan-scope caveat + `--no-caveat` opt-out — MERGED (PR #105)

## Wave 3 — security (code-only)
- [ ] #78 SHA-pin all GH actions (#1/#2); add `permissions: {contents: read}` to test.yml+lint.yml (#4); atomic 0600 create + PSC_API_KEY env + loud `--insecure` stderr warn (#5/#6). Document infra-only asks (#3 dependabot alerts). Findings #7/#8/#9 = no action.

## Wave 4 — workbench parity (serialize on app.py/session.py)
- [ ] #84 value column in find results
- [ ] #86 where-used for ALL selected + owning-object column (bug)
- [ ] #87 move destination drop-down (shared|any DG)
- [ ] #89 rename: choose which selected entry
- [ ] #91 remove single item from selection panel
- [ ] #93 SET mode: write combined set script to file
- [ ] #92 offline apply: partial-config output option (core apply_xml + source + session)
- [ ] #88 staged changelist: inspect + drop individual staged change
- [ ] #94 object creation screen(s) (reuse crud.plan_*; blockers gate)
- [ ] #85 dedup: keep/drop chooser + 3+ collapse + DG drop-down
- [ ] #83 profile CRUD in TUI (full; ruamel persist via config/loader)
- [ ] #95a net-new screen: `refs unused`
- [ ] #95b net-new screen: `refs dangling`
- [ ] #95c net-new screen: `name lint`
- [ ] #95d net-new screen: `name apply` (rename-to-scheme)
- [ ] #95 close umbrella once matrix satisfied

## Wave 5 — docs (batched; never blocks a merge)
- [ ] #90 workbench guide (docs site + mkdocs nav), README section, CHANGELOG
- [ ] docs pass for every new capability (diff, ndjson, cascade, audit modes, resolve-fqdn, security)

## Wave 6 — release
- [ ] Bump `psc/_version.py` + `pyproject.toml` to 1.0.0; sync uv.lock; finalize CHANGELOG `## v1.0.0`
- [ ] Full suite green on main; `git tag v1.0.0` → push → release.yml publishes to PyPI
- [ ] Final report to user
