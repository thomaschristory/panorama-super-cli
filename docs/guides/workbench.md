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
| `v` | **inspect** | Open the focused results row read-only: its member tree and effective leaves (the TUI form of [`show`](finding-objects.md#open-an-object)). Nested groups start collapsed — drill in with enter. Acts on the highlighted row, no selection needed. |
| `m` | **move** | Promote selected objects toward `shared`; a destination drop-down offers the valid ancestors. |
| `x` | **decommission** | Reference-safe cascading teardown of the selected addresses. |
| `r` | **rename** | Reference-aware rename; choose which selected entry to rename and its new name. |
| `e` | **rule** | Add the selected objects as members of an existing rule field. |
| `G` | **group** | Add the selected objects as members of an existing address-/service-group (the TUI form of [`group edit-member --add`](editing-objects.md#edit-group-membership); removal is CLI-only). |
| `N` | **new group** | Build a *new* group out of the selection (see [below](#n-a-group-from-the-selection)). The kind follows what you picked — addresses make an address-group, services a service-group — and the location picker defaults to the narrowest one that can see every member. |
| `c` | **create** | Object creation (address / group / service / service-group / tag), the TUI form for `psc set`. The form is **dynamic** — it shows only the fields the chosen kind uses, and predefined values (address type, service protocol, tag color) are **dropdowns**. |
| `i` | **refs-unused** | List objects no rule reaches (read-only). |
| `g` | **dangling** | List references to names that resolve to nothing (read-only). |
| `l` | **name-lint** | Report objects that drift from the configured naming scheme. |
| `n` | **name-apply** | Rename drifting object(s) to their scheme name; choose an entry to apply. |
| `p` | **profiles** | CRUD live connection profiles, persisted to `~/.psc/config.yaml`. Also switches the active source (`ctrl+r`) — reload the session onto the focused profile or an offline export path; discards the selection + staged batch (with a confirm when a batch is staged). |
| `s` | **staged** | Inspect the staged changelist, drop individual changes, and **apply** (`ctrl+a`) — the only place apply is reachable (see below). |

The mutating spokes are the same engines as their CLI counterparts, so the
behaviour — and the [blockers](safety.md#blockers-are-a-hard-gate) that refuse an
unsafe plan — is identical. A spoke with an empty or unusable selection rings the
bell instead of staging.

## `N` — a group from the selection

The find session's payoff: search, `space` the objects you want, `N`, name the
group, `ctrl+y`. `G` adds the selection to a group that already exists; `N` makes
one out of it.

```text
search: 10.0.5.               →   ▸ web-srv-01  shared  10.0.5.10/32   [x]
                                  ▸ web-srv-02  shared  10.0.5.11/32   [x]
                                  ▸ web-srv-03  shared  10.0.5.12/32   [x]

N   →   New address-group from web-srv-01, web-srv-02, web-srv-03
        name:     web-tier
        location: shared          ← the narrowest location that sees every member
        ctrl+y  →  set shared address-group web-tier static [ web-srv-01 web-srv-02 web-srv-03 ]
```

The **kind is derived from the selection**: addresses and address-groups make a
static address-group (a group nested inside a group is valid PAN-OS and allowed);
services and service-groups make a service-group. A selection that mixes the two
namespaces belongs in no group and is refused, as is a tag. Staging clears the
selection — its members have been consumed into the group.

Group members are **bare names, resolved upward** from the group's own location,
which gives two ways to write a group that does not mean what you picked. `N`
refuses both:

- **A member the location cannot see.** A `shared` group naming an object that
  lives in `DG-NYC` dangles — `shared` cannot see into a device-group, and
  neither can a sibling. This is why the location picker defaults to the
  narrowest location whose visibility cone (itself, its ancestors, `shared`)
  covers every member; when the selection spans *sibling* device-groups no such
  location exists, and every choice blocks.

    ```text
    BLOCKED: member 'nyc-lb' @DG-NYC is not visible from shared — a group can
    only name objects in its own location, its ancestors, or shared
    ```

- **A member whose name is shadowed there.** You selected `web` @shared, but the
  group lives in `DG-NYC`, which defines its own `web`: the group would bind to
  *that* one. PAN-OS has no syntax for "the shared one", so the intent is
  inexpressible and the plan is refused rather than quietly pointed at the wrong
  object. (Selecting `DG-NYC`'s own `web` is fine — that *is* what the group
  resolves to.)

A group of that kind already at that name and location also blocks: `N` creates,
and growing an existing group is `G`. A same-named group *elsewhere* in the
hierarchy is legal and only warns — the two shadow each other, and a bare
reference resolves to whichever is nearest.

The CLI equivalent is [`psc set address-group`](editing-objects.md#create-and-update-objects),
which takes member names directly. It has no visibility blockers: it cannot know
which object you meant by a name, only the workbench's selection carries that.

## The staged changelist

Every stage compounds into a git-like changelist. The hub shows only a
`staged (N)` counter; the **staged** spoke (`s`) is the full view:

- inspect any staged change to see its complete rendered `set`-script,
- **drop a single change** (`d`) without discarding the rest of the batch (if the
  dropped change is a dependency of a later one, the drop is refused and the batch
  is kept intact),
- **apply the batch** (`ctrl+a`) — the only place apply is reachable, so you
  always review what's staged before emitting it (see below).

Because each plan is built against the working config *with the prior stages
already applied*, the batch is internally consistent — no stage silently
invalidates another.

## Applying the batch

Apply is reached **only from the staged changelist** (`s`), so you can't emit a
batch you haven't looked at: open the staged spoke, then `ctrl+a` opens the
**apply screen**, where you choose the output format and destination *after*
reviewing the batch — no need to decide at launch. Pick one:

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
