# move objects toward shared — design (issue #74)

## Problem

While cleaning a Panorama, objects are routinely stranded in a device-group
when their natural home is `shared` (or a parent device-group that several
siblings inherit from). Today `psc` can find, dedup, rename, audit, and
decommission objects, but it cannot *relocate* one — the user has to delete and
recreate by hand, which is exactly the reference-unsafe operation `psc` exists
to make safe.

A "move" is, in `ChangeSet` terms, **create-at-destination + delete-at-source**.
The danger is identical to merge/rename: references are by *name*, resolved up
the device-group chain (`Snapshot.ancestors`), with nearer definitions
shadowing farther ones. A naive move can dangle references or silently change
which object a rule matches.

## Scope (confirmed with the user)

1. **Direction: promote *toward* shared only.** `--to` must be `shared` or a
   *strict ancestor* of `--from`. This is the safe-by-construction direction:
   `shared`/an ancestor is in the chain of every location in `--from`'s subtree
   and is *farther* than `--from`, so any reference that resolved to the object
   at `--from` still resolves to it at the destination — **no repoint needed**.
   A sibling / unrelated / child destination would orphan references in
   `--from`'s scope, so it is a hard **blocker**, not supported in v1.
2. **Collision at destination → identical-value merges, different-value blocks.**
   - address / address-group: delegate to the existing `dedup.plan_merge` /
     `plan_merge_group` (`keep`=destination, `drop`=source). Those planners
     already implement exactly "identical → repoint+delete, different → block",
     so the policy is reused, not re-derived.
   - service / service-group / tag: any name collision at the destination is a
     **blocker** in v1 (no merge planner exists for these kinds yet) — message
     points the user at removing/renaming one side first. *Limitation, not a
     silent gap.*
3. **Dependencies must already be visible at the destination — no auto-cascade.**
   The moved object's own downward references (a group's `static` members, an
   object's `tags`, a service-group's members, a dynamic group's filter tags)
   must each resolve from the destination. Any that don't → **blocker** listing
   exactly what to move first.
4. **Single object per invocation.** Bulk/filtered moves are a follow-up.
5. Dry-run by default; `--apply` to commit; same `complete()` contract as every
   other mutating command.

## Key insight

Because the move is restricted to *promote-toward-shared*, the create-at-dest +
delete-at-source plan needs **zero reference edits**. Every reference that
pointed at the object falls through, by ordinary PAN-OS shadowing, onto the new
(farther-up) definition once the source copy is removed — *provided* nothing
between source and destination already defines the name. That one caveat is the
**intermediate-shadow guard**: if any device-group strictly between `--from` and
`--to` already defines the name (same namespace), removing the source would
re-resolve references to that intermediate instead of the destination —
**blocker**.

This is why the engine is small: the two genuinely hard cases (collision,
shadowing) are delegated to `dedup` / handled by a single guard; the common
case is one `ObjectUpsert` + one `ObjectDelete`.

## Architecture

New engine `psc/core/relocate.py` (pure, returns a `ChangeSet`) + one thin CLI
command `psc/cli/move_cmds.py`. No new `ChangeSet` op kind, no new applier code —
`ObjectUpsert`/`ObjectDelete` already render (`setcmd`) and apply
(`apply_xml`/`apply_live`).

### `relocate.plan_move(snapshot, graph, *, kind, name, source_name, dest_name) -> ChangeSet`

Order of checks (each returns a blocked, zero-op `ChangeSet` on failure, per the
repo invariant):

1. **Existence.** Object of `kind` named `name` defined *directly* at `source`.
   Absent → blocker.
2. **Direction.** `dest` must be in `ancestors(source)` and `dest != source`.
   Otherwise → blocker ("`move` only promotes toward shared…").
3. **Intermediate-shadow guard.** No location strictly between `source` and
   `dest` may already define `name` in the object's namespace → blocker.
4. **Dependency gate.** Every downward dependency must `graph.resolve(...)` from
   `dest`. Unresolved ones → blocker listing them.
5. **Collision dispatch.**
   - destination has no same-kind `name` → **clean promote**: build the
     destination `ObjectUpsert` by calling the matching `crud.plan_*` with
     `location=dest` (reuses validation + the leaf-key contract + cross-kind
     collision check), then append `ObjectDelete` at `source`. If destination is
     `shared`, add a warning naming any currently-*dangling* references
     elsewhere that will now newly resolve to the promoted object.
   - destination has same-kind `name`:
     - address → `dedup.plan_merge(keep@dest, drop@source)`
     - address-group → `dedup.plan_merge_group(keep@dest, drop@source)`
     - service / service-group / tag → blocker (v1 limitation).
6. `gate_unmappable_reference_edits(cs)` (harmless on the clean path; the
   delegated dedup planners already gate internally), then enforce the
   zero-op-when-blocked invariant.

The namespace per kind mirrors `naming.plan_rename` / `refs`: address &
address-group → `address`, service & service-group → `service`, tag → `tag`.

### CLI: `psc move <kind> <name> --from <loc> --to <loc> [--apply] [--out] [-of]`

`kind` is an `ObjectKind` positional (`address|address-group|service|service-group|tag`);
`--from`/`--to` are required location strings (`shared` or a device-group name);
dry-run by default. Wired as a single top-level command (like `decommission`),
not a sub-app. Delegates rendering/apply to `cli/_plan.complete`.

## Testing (TDD; safety-critical paths first)

Engine (`tests/test_relocate.py`):
- clean promote DG→shared: one upsert@shared (exists=False) + one delete@source;
  no reference edits; referrers still resolve (assert via a rebuilt graph or by
  inspecting ops).
- promote DG→ancestor DG (nested fixture).
- sibling/child/unrelated destination → blocked.
- dest == source → blocked.
- object absent at source → blocked.
- collision, identical value → merge plan (delete source, no second copy).
- collision, different value → blocked.
- intermediate-shadow guard → blocked.
- dependency not visible at dest (group member / tag) → blocked, names listed.
- promote-to-shared revives a sibling dangling ref → warning emitted.

CLI (`tests/test_cli_move.py`):
- dry-run prints plan, exits 0, writes nothing.
- `--apply --out` offline round-trips (object now under destination scope, gone
  from source) using `apply_xml`.
- blocked plan → exit 6 (`CONFLICT`), nothing written.
- `-o set` renders create+delete lines.

## Out of scope (v1)

- Moving *away* from shared / to a sibling or child (would require repointing or
  copying — deliberately blocked).
- Auto-cascading dependencies.
- service/service-group/tag identical-value merge on collision.
- Bulk / filtered selection.
