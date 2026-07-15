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
- `psc/core/promote.py` — collapse a cross-DG duplicate *bucket* into `shared` (or
  a common ancestor): one upsert, N deletes, zero repoints (upward promotion falls
  through by shadowing). Composes `dedup` (buckets, repointing) and `relocate`
  (direction/shadow/dependency gates, cascade). The `--keep` rename path resolves
  against a *synthetic* snapshot that already carries the destination object —
  without it, the survivor is invisible where it will actually live.
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
- `psc/skill/` — access to the bundled portable Skill (`bundle_path()`,
  `BUNDLE_NAME`); resolves `skills/panorama-super-cli/SKILL.md` in both the
  source tree and the installed wheel via `importlib.resources`. Backs
  `psc skill install/export` (`psc/cli/skill_cmds.py`).
- `psc/output/` — formatters (table/json/jsonl/yaml/csv/set) + error envelope.
- `psc/config/` — profiles + defaults (ruamel round-trip).
- `psc/cli/` — the Typer app; one thin command module per feature group.
- `psc/tui/` — the workbench (interactive TUI), a second frontend over
  `psc.core`:
  - `app.py` — `WorkbenchApp` + `HubScreen`, the hub screen every spoke stacks
    on top of.
  - `session.py` — `WorkbenchSession`: search, selection buffer, staged
    changelist, over a `Source`.
  - `state.py` — TUI-local state models (selection items, output mode).
  - `commands.py` — **the single source of truth** for the hub: one `Command`
    row per action (key, handler, title, description, category). `BINDINGS`,
    the `_HUB_ACTIONS` spoke-stacking guard, the `?` keymap overlay, and the
    `ctrl+p` command palette are all *derived* from this table — add a spoke
    by adding a row here, not by touching four files.
  - `palette.py` — `PscCommands`, the `ctrl+p` command-palette `Provider`
    reading `commands.py`.
  - `screens/` — one module per spoke (dedup, move, rename, create, …), plus
    `keymap.py` for the `?` overlay.

The hard rule: **`psc/core/` imports nothing from `psc/cli/` or any UI
framework.** Engines return models; the CLI formats them. If you reach for
`typer`/`rich` inside `core/`, you're in the wrong layer. `psc/tui/` *is*
allowed to depend on `psc/core/` and Textual — it's a frontend like `psc/cli/`,
not part of the engine layer.

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
- **Workbench spoke-stacking guard.** `WorkbenchApp.check_action` refuses any
  hub action while a spoke screen is on top of the stack (`len(screen_stack) >
  1`). This is not a formality: Textual's *modal* screens do not block
  app-level `priority` bindings — Textual's own priority `ctrl+q` reaches
  through a modal, and so would `?` (a priority binding, see `commands.py`)
  without this guard, letting a second spoke stack over the first and stage a
  plan against an already-stale one. `check_action` is the only thing
  preventing that; don't rely on modality alone when adding a priority key.

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
