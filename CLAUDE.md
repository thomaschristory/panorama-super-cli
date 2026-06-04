# Working on panorama-super-cli (for AI agents)

Contributor guide for AI agents (and humans) modifying this repo. The
*end-user* agent guide is the bundled Skill at
`skills/panorama-super-cli/SKILL.md` — that one is about *using* `psc`; this one
is about *changing* it.

## Architecture cheat sheet

The hard split is **backend (`psc/core`) vs frontend (`psc/cli`)**. A future web
UI would import `psc.core` directly and never touch `psc.cli`.

- `psc/core/models.py` — framework-free Pydantic domain model (`Address`,
  `AddressGroup`, `Service`, `ServiceGroup`, `Tag`, `SecurityRule`, `NatRule`,
  `Snapshot`, `Location`). The lingua franca; imports nothing from the rest.
- `psc/core/parse.py` — Panorama config XML → `Snapshot` (via `defusedxml`).
- `psc/core/normalize.py` — IP/value canonicalization + matching (the numeric
  heart of `find` and `dedup`).
- `psc/core/refs.py` — the reference graph: where-used, unused (recursive),
  dangling. Models PAN-OS name resolution (DG-local shadows shared).
- `psc/core/resolve.py` — `find` engine (IP/value/name → objects).
- `psc/core/dedup.py` — duplicate detection + safe merge planning (objects and
  address-groups, the latter bucketed by effective leaf-address set).
- `psc/core/audit.py` — overlap/containment audit over address ranges (pure
  read; ip-netmask/ip-range only).
- `psc/core/crud.py` — single-object create/update validation + `ObjectUpsert`
  planning (name/desc/value-kind/port/color rules; cross-kind + type/mode-change
  blockers).
- `psc/core/rule_edit.py` — idempotent rule-field member edits (removal lowers to
  delete-field + re-set because PAN-OS `set` appends).
- `psc/core/decommission.py` — reference-safe cascading teardown of address
  objects (scrub groups → scrub rules → orphan-rule delete → emptied-group delete
  → object delete, to a fixpoint).
- `psc/core/naming.py` — opt-in naming templates + reference-aware rename.
- `psc/core/changeset.py` — the inspectable mutation plan every write produces
  (`ObjectUpsert` / `ReferenceEdit` / `ObjectRename` / `ObjectDelete` /
  `RuleDelete`, plus `blockers`/`warnings`).
- `psc/core/setcmd.py` — render objects/changesets as PAN-OS `set` commands.
- `psc/core/apply_xml.py` — apply a `ChangeSet` to config XML (offline `--apply`).
- `psc/core/apply_live.py` — lower a `ChangeSet` to XML-API xpath ops (live
  `--apply`); pure/device-free, so the xpath construction is unit-testable.
- `psc/core/source.py` — `OfflineSource` (file) / `LiveSource` (pan-os-python);
  live `apply` pushes to the candidate config and never commits.
- `psc/output/` — formatters (table/json/jsonl/yaml/csv/set) + error envelope.
- `psc/config/` — profiles + defaults (ruamel round-trip).
- `psc/cli/` — the Typer app; one thin command module per feature group.

The hard rule: **`psc/core/` imports nothing from `psc/cli/` or any UI
framework.** Engines return models; the CLI formats them. If you reach for
`typer`/`rich` inside `core/`, you're in the wrong layer.

Features are deliberately independent: `find_cmds`, `dedup_cmds`, `refs_cmds`,
`name_cmds`, `audit_cmds`, `set_cmds`, `rule_cmds`, `decommission_cmds` each map
to one `core` engine and can be deleted without touching the others. Reuse lives
in `core` (models, refs, changeset, setcmd).

## Common commands

- `just sync` — install/refresh deps.
- `just test` — run all tests.
- `just lint` — ruff + mypy --strict.
- `just fix` — auto-fix ruff issues.
- `just psc <args>` — run the local CLI.
- `just sync-agents` — regenerate AGENTS.md from CLAUDE.md.

## Conventions

- Python 3.12+, full type annotations, `mypy --strict`.
- Pydantic v2 for all structured data.
- Conventional Commits; squash-merge; `main` is always releasable.
- TDD: write the failing test first. The safety-critical paths (merge
  repointing, blockers, apply round-trip, shadow-rename refusal) MUST have tests.
- Comments explain non-obvious *why*, never *what*.

## Safety model (do not regress)

- **Dry-run is the default.** Mutating commands print a plan and exit without
  writing unless `--apply` is passed.
- **Repoint before delete.** A merge/rename rewrites every referencing group,
  security rule, and NAT rule *before* removing the object.
- **`ChangeSet.blockers` is a hard gate.** A non-empty `blockers` list means the
  executor refuses to apply, even with `--apply`. Add a blocker rather than
  silently doing something surprising (e.g. a cross-scope reference that can't
  be repointed, or a shared-rename that would shadow a device-group object).
- **Offline `--apply` never overwrites the source export** — it writes to `--out`.

## Error contract

Expected failures raise `PscError(message, ErrorType.…)`. Exit codes are part
of the public contract (`psc/output/errors.py::EXIT_CODES`) — don't renumber
without a major bump. Machine output is never rich-wrapped (`soft_wrap=True`).

## Branching

`main` is protected; work on short-lived `feat/…`, `fix/…`, `docs/…` branches,
open a PR, get CI (`lint`, `test`) green, squash-merge. Releases are `vX.Y.Z`
*tags* on `main` (never branches); the tag triggers `release.yml` →
PyPI (trusted publishing) + the docs site.

Keep `psc/_version.py` and `pyproject.toml` `version` in agreement; the release
workflow validates the tag against `psc/_version.py`.

## Coordinating work on issues and PRs

When you start on an issue or PR, comment so other agents see it's claimed
(`gh issue comment <n> -b "…"`). Post terse updates on claim, milestones
(branch pushed, PR opened, CI green), blockers, and completion.
