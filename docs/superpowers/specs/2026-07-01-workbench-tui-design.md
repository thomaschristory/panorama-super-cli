# Workbench — interactive Textual TUI for `psc`

**Date:** 2026-07-01
**Status:** Approved design (pre-implementation)
**Entry point:** `psc workbench` (alias `psc w`)

## Summary

An interactive terminal UI ("the workbench") that glues the existing `psc`
engines together around a single, persistent, **heterogeneous selection
buffer**. You search for objects, multi-select them into the buffer, then route
that selection into an action (dedup, usage, rule create/edit) without
copy-pasting names between commands. Mutations accumulate into a git-like
**staged changelist** that you review together and apply as one batch.

The workbench is a new **frontend** beside the CLI. It imports only
`psc.core` (engines) and `psc.output` (formatters) — never `psc.cli`. All
safety-critical logic already lives in the engines; the TUI orchestrates them.

## Goals

- Remove copy-paste friction between `find` → act (`dedup` / `refs` / rule ops).
- Let a single selection be built from **mixed object kinds** (addresses,
  address-groups, services, service-groups, tags) and spent across actions.
- Compose several edits, review them together, apply once — WYSIWYG and safe.
- Preserve every CLI safety invariant (dry-run default, blockers hard-gate,
  repoint-before-delete, candidate-only pushes).

## Non-goals (v1)

See **Roadmap / out of scope** below.

## Architecture

### Layer placement

New `psc/tui/` package, sibling to `psc/cli/`.

- Imports **only** `psc.core` (engines: `resolve`, `dedup`, `refs`, `crud`,
  `rule_edit`, `changeset`, `apply_xml`, `source`, `parse`) and `psc.output`
  (set-command rendering / formatters).
- Imports **nothing** from `psc/cli`. Honours the existing hard rule: core is
  UI-framework-free; the TUI is just another frontend, exactly as a future web
  UI would be.
- If `typer`/`rich`/`textual` logic leaks into `psc/core`, that's the wrong
  layer — unchanged from today's contract.

### Dependency

`textual` is a new dependency, shipped as an **optional extra**:
`pip install panorama-super-cli[tui]`. The base CLI stays lean; `psc workbench`
prints a friendly "install the `tui` extra" message if Textual is absent.

### Entry point

Thin command in `psc/cli/app.py`: `psc workbench` (alias `psc w`) constructs the
session (loads profile, opens source) and launches the Textual `App`. All real
work happens in `psc/tui/`.

### Theme

Palo Alto **orange** as the accent color, via a Textual CSS theme
(`psc/tui/workbench.tcss`). Keyboard-first; mouse works via Textual defaults.

## Session state

One in-memory container shared by every screen (injected, not global):

| Field | Contents |
|---|---|
| `profile` | loaded psc profile → source (offline file / live host+creds), default DG, default output mode |
| `output_mode` | `set` \| `offline-apply` \| `live-apply`; defaults from profile, overridable in-app |
| `working_xml` | mutable config as XML text; initialised to `source.raw_xml()` |
| `working_snapshot` | `parse(working_xml)`; re-derived after each stage; **everything the UI displays reads from this** |
| `selection` | ordered, heterogeneous buffer of object refs (mixed kinds) |
| `staging` | ordered list of `(label, ChangeSet)` — the git-like changelist |

**Source/output policy:** profile-driven, overridable. Source comes from the
selected profile. `output_mode` defaults from the profile and is toggled in-app.

## Staging engine (the core of the glue)

Makes "do several things then apply" safe and WYSIWYG by compounding every edit
onto an in-memory working config. Reuses the existing pure
`apply_changeset(xml_text, cs) -> str`.

1. A mutating spoke plans a `ChangeSet` against the **current
   `working_snapshot`** (not the original), so it already sees prior staged
   edits.
2. Staging applies it in memory:
   `working_xml = apply_changeset(working_xml, cs)`, then re-parse →
   new `working_snapshot`. The **selection buffer is reconciled** against the new
   snapshot (objects that no longer exist — e.g. merged-away dupes — drop out;
   survivors stay).
3. The `ChangeSet` is appended to `staging` for the audit trail and final apply.

Because each stage is planned against reality, you can never stage a change that
references something a prior stage deleted. No late conflict surprises.

### Apply batch (only step that touches the real target)

- `output_mode = set` → render the combined staged changesets as one
  `set`-command script via `psc.output`; display / write it. Nothing pushed.
- `offline-apply` → write `working_xml` to `--out`. **Never overwrites the
  source export** (same rule as the CLI).
- `live-apply` → replay each staged `ChangeSet` in order via `LiveSource.apply`
  to the **candidate** config. Never commits.

## Safety model (inherited, do not regress)

- Nothing touches a device until **apply-batch** — staging is all in-memory XML;
  dry-run is effectively the default.
- **`ChangeSet.blockers` is a hard gate**, both per-stage and at batch apply. A
  blocked changeset cannot be staged or applied. Same refusals as the CLI
  (repoint-before-delete, shadow-rename refusal, cross-scope reference that
  can't be repointed) — inherited free because blockers live in the engines.
- Apply-batch requires an explicit confirm keystroke and re-shows the whole
  batch's blockers/warnings first.
- Live pushes go to the candidate config only; the workbench never commits.

## Screens

### Hub (home screen)

```
┌─ psc workbench ──────────────────── profile: prod-pano · live · out:set ─┐
│ search: 10.0.5.0/24                          kinds:[addr][grp][svc][tag] │
├─────────────────────────────┬────────────────────────────────────────────┤
│ RESULTS (7)                 │ SELECTION (3)                    [clear]     │
│ [x] addr  web-srv-01        │  addr  web-srv-01                            │
│ [x] addr  web-srv-02        │  addr  web-srv-02                            │
│ [ ] addr  db-gw             │  grp   app-lb-pool                           │
│ [x] grp   app-lb-pool       │                                              │
│ [ ] svc   tcp-8443          │  ── staged (2) ──────────── [review][apply] │
│ ...                         │  ✓ merge web-srv dupes                       │
├─────────────────────────────┴────────────────────────────────────────────┤
│ act: [d]edup  [u]sage  [r]ule  ·  [space]select [/]search [tab]panes [?]  │
└────────────────────────────────────────────────────────────────────────────┘
```

- **Search** runs the `resolve`/find engine over `working_snapshot`. A **kind
  filter** (addr / grp / svc / svc-grp / tag) selects which kinds appear;
  search by IP, value, or name across kinds, results in one list.
- **Multi-select** (`space`) pushes rows into the heterogeneous selection, which
  persists across every screen.
- The **staging strip** is always visible on the hub; `review` opens the full
  staged diff, `apply` runs apply-batch.
- The actions row routes the current selection into a spoke.

### Spokes (each = one `psc.core` engine, entered carrying the selection)

Uniform contract: **filter selection to the kinds I handle → show plan/result →
(mutating ones) stage a ChangeSet → return to hub.** Skipped kinds are reported.

- **Dedup** — filters selection to addresses + address-groups (groups bucketed
  by effective leaf-address set, as `dedup.py` does), shows the safe merge plan
  (survivor + repoints), `stage` appends the ChangeSet. Reports ignored kinds.
- **Usage / refs** *(read-only, never stages)* — where-used / unused / dangling
  for the selection; rows are actionable (jump a referrer back into selection).
- **Rule create/edit** — sorts the selection into **source / dest / service**
  slots by kind; a form covers the rest (name, action, zones…); builds via
  `crud` / `rule_edit`, `stage` appends. Edit mode adds/removes selected members
  on an existing rule (idempotent, per the engine).

### Shared widgets

- **ReviewPanel** — used by every mutating spoke and by staging `review`.
  Renders `set` commands + `warnings` + `blockers`; a non-empty blockers list
  disables the stage/apply key.
- **SelectionList** — the heterogeneous selection view, reused on hub and spokes.

## Textual specifics

- One `App`; a `HubScreen`; one `Screen` per spoke; reusable `ReviewPanel` and
  `SelectionList` widgets; orange theme in `workbench.tcss`.
- The session object is injected into screens. Screens call engines and mutate
  session state; no engine logic lives in widgets (mirrors "CLI formats, engines
  compute").

## Testing

- **Engines keep their existing unit tests.** The TUI adds no new
  safety-critical logic — it orchestrates engines.
- **TUI tests** use Textual's `Pilot` async harness for key flows against an
  offline XML fixture: search → select → stage → apply-batch; blockers disable
  apply; selection reconciles after a staged merge.
- Because staging is pure XML-in / XML-out, the entire compounding flow is
  testable headless with no device.

## Roadmap / out of scope (v1)

**Later spokes** (each drops in against its existing engine with the same
enter-with-selection → filter → plan → stage contract; no architecture change):

1. **Naming / rename** — `naming.py` (templates + reference-aware rename)
2. **Move to shared** — `relocate.py` (cross-scope relocation)
3. **Decommission** — `decommission.py` (cascading reference-safe teardown;
   destructive → after the safe spokes are solid)
4. **Audit (overlaps)** — `audit.py` (overlap/containment report; read-only)

**Explicitly out of scope:**

- **Commit/push to device** — workbench pushes to the candidate config only,
  never commits, exactly like the CLI.
- **Mouse-first UX** — keyboard-first; mouse works via Textual defaults but is
  not the target.
- **Reimplementing CLI-only commands** — the workbench is glue over the
  high-value flows, not a full CLI replacement.

## Open implementation notes

- `psc/config` profile loading may need a small accessor usable outside the
  Typer command context (the TUI constructs the session directly).
- Confirm `LiveSource.apply` can be called repeatedly for a staged batch
  (replay N changesets in order) without re-fetching between pushes.
