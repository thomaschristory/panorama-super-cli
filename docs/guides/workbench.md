# Workbench (interactive TUI)

The **workbench** is `psc`'s interactive [Textual](https://textual.textualize.io/)
terminal UI — a keyboard-driven cockpit that glues every engine together around a
persistent **selection buffer** and a git-like **staged changelist**. It is at
full CLI parity: everything you can do from the command line you can do here, and
mutations batch into one reviewed apply.

```console
psc --config panorama.xml workbench
psc -p prod w                          # `w` is the short alias
```

It is a pure `psc.core` / `psc.output` frontend — it never imports the CLI layer
— so it inherits **every** safety invariant unchanged: dry-run by default, a hard
[blocker gate](safety.md#blockers-are-a-hard-gate), repoint-before-delete, offline
never overwrites the source export, and live never commits.

## Launching

The source is chosen the same way as any other command:

```console
psc --config panorama.xml workbench    # offline: an exported config
psc --profile prod workbench           # live: a configured profile
psc workbench                          # live: the default profile
```

The **output mode** decides how a staged batch is finally applied — pick it at
launch (or change your mind before applying by relaunching):

| `--output-mode` | What "apply" does |
| --- | --- |
| `set` (default) | Render the combined PAN-OS `set` script; push nothing. |
| `offline-apply` | Write the compounded config to `--apply-out <file>`. |
| `live-apply` | Push the batch to the live **candidate** (never commits). |

```console
psc -c panorama.xml workbench --output-mode offline-apply --apply-out fixed.xml
psc -p prod workbench --output-mode live-apply
```

Passing `--apply-out` implies `offline-apply`; choosing `offline-apply` without
`--apply-out` fails fast at launch rather than at apply time.

## The hub

The home screen is a **hub**: a search box, a results table, the selection
buffer, and a `staged (N)` strip.

```
┌ search: IP / value / name ─────────────────────────────┐
├──────────────────────────┬─────────────────────────────┤
│ results                  │ selection                   │
│ kind  name  location  …  │ kind  name  location        │
│                          ├─────────────────────────────┤
│                          │ staged (N)                  │
└──────────────────────────┴─────────────────────────────┘
```

The flow is **hub → search → select → spoke → staged changelist → apply**:

1. **Search** by IP, CIDR, range, value, or name; matches fill the results table.
2. **Select** the rows you want to act on into the selection buffer.
3. Open a **spoke** (dedup, move, rename, …) that consumes the selection and
   builds a plan you review.
4. **Stage** the plan — it compounds into the changelist (each new plan is built
   against the *already-staged* working config, so plans never go stale).
5. **Apply** the whole batch at once, in your chosen output mode.

While a spoke is open, the hub keys are inert — you must finish or cancel the
spoke first. This prevents a second spoke stacking over the first and letting a
plan go stale.

## The selection buffer

The selection is **heterogeneous** (addresses, groups, services, tags can coexist)
and **persistent** across spokes — build it once, route it into several spokes.

| Key | Action |
| --- | --- |
| `space` | Toggle the highlighted results row in/out of the selection. |
| `delete` / `backspace` | Drop the focused row from the selection panel. |

## Spokes

Each spoke maps to one core engine. Read-only spokes just report; mutating spokes
build a `ChangeSet` you review and **stage** (`ctrl+y`) or cancel (`escape`).

| Key | Spoke | What it does |
| --- | --- | --- |
| `d` | **dedup** | Collapse the duplicate bucket in the selection toward a chosen survivor (whole-bucket merge; the rest are repointed and removed). A dropdown picks the survivor — its label is `name@location`, so the choice is also the scope. |
| `D` | **duplicates scan** | Config-wide duplicate buckets (read-only), with a kind toggle for addresses / services / address-groups. The discovery counterpart of `d`: `d` merges the selection, `D` finds every duplicate in the config. |
| `u` | **usage** | Where-used for the whole selection (read-only), with an owner column naming which selected object each reference resolves to. |
| `a` | **audit** | Read-only, with a mode toggle: address overlap/containment involving the selection, or custom services duplicating a well-known / predefined port. |
| `f` | **diff** | Device-group-vs-device-group drift (read-only): added/removed/changed objects between two scopes, picked from dropdowns. |
| `o` | **export** | Write objects of one kind to an NDJSON file (read-only export; never overwrites the source config). |
| `m` | **move** | Promote selected objects toward `shared`; a destination drop-down offers the valid ancestors. |
| `x` | **decommission** | Reference-safe cascading teardown of the selected addresses. |
| `r` | **rename** | Reference-aware rename; choose which selected entry to rename and its new name. |
| `e` | **rule** | Add the selected objects as members of an existing rule field. |
| `c` | **create** | Object creation (address / group / service / service-group / tag), the TUI form for `psc set`. |
| `i` | **refs-unused** | List objects no rule reaches (read-only). |
| `g` | **dangling** | List references to names that resolve to nothing (read-only). |
| `l` | **name-lint** | Report objects that drift from the configured naming scheme. |
| `n` | **name-apply** | Rename drifting object(s) to their scheme name; choose an entry to apply. |
| `p` | **profiles** | CRUD live connection profiles, persisted to `~/.psc/config.yaml`. Also switches the active source (`ctrl+r`) — reload the session onto the focused profile or an offline export path; discards the selection + staged batch (with a confirm when a batch is staged). |
| `s` | **staged** | Inspect the staged changelist (see below). |

The mutating spokes are the same engines as their CLI counterparts, so the
behaviour — and the [blockers](safety.md#blockers-are-a-hard-gate) that refuse an
unsafe plan — is identical. A spoke with an empty or unusable selection rings the
bell instead of staging.

## The staged changelist

Every stage compounds into a git-like changelist. The hub shows only a
`staged (N)` counter; the **staged** spoke (`s`) is the full view:

- inspect any staged change to see its complete rendered `set`-script,
- **drop a single change** (`d`) without discarding the rest of the batch (if the
  dropped change is a dependency of a later one, the drop is refused and the batch
  is kept intact).

Because each plan is built against the working config *with the prior stages
already applied*, the batch is internally consistent — no stage silently
invalidates another.

## Applying the batch

`ctrl+a` opens the **apply screen**, where you choose the output format and
destination *after* reviewing the batch — no need to decide at launch. Pick one:

- **Print the set script here** — the combined PAN-OS `set` script, shown inline.
- **Save a set-command file (`.set`)** — the same script written to a file.
- **Save a full XML config** — the whole compounded config (offline apply).
- **Save a minimal partial XML config** — only the touched subtrees (offline apply).
- **Push to the live candidate** — pushes over the XML API and **never commits**
  (offered only when the session is backed by a live profile).

The `--output-mode` / `--apply-out` launch flags still work; they just pre-select
the default here, overridable in-app. The set-script options are exports (they
keep your staging); a full/partial config write or a live push commits the batch
and clears staging. A **live push** and **overwriting an existing file** each need
an explicit second `ctrl+a` to confirm.

The safety model is unchanged: a blocked batch is refused, an offline write never
overwrites the source export, and a live push never commits. Any apply failure is
surfaced on the screen; the app does not crash and your batch is preserved.

## Quitting

`q` quits. The staged changelist lives only for the session — apply it (or write
it to a `set`/config artifact) before you leave.
