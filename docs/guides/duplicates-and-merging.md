# Duplicates and merging

## Find duplicates

```console
psc -c panorama.xml dedup addresses
psc -c panorama.xml dedup services
```

Objects are grouped by **value**, not name. `h-web1`, `web-primary`, and
`h-web1-slash` (all `10.0.0.10/32`) land in one bucket; `tcp-443` and
`svc-https` (both TCP/443) in another. Each row tells you the value and every
object name + location that defines it.

### Strict by default

Address matching is **strict**: only byte-identical values are duplicates. A
host accidentally written with a subnet mask — `web-server = 10.1.1.50/24` —
is **not** reported as a duplicate of the network `internal = 10.1.1.0/24`,
even though both mask down to the same `/24`. (`10.0.0.10` and `10.0.0.10/32`
*are* still the same host, so they group.)

Pass `--not-strict` for the looser, fringe behaviour that masks host bits and
collapses a host-with-mask onto its network:

```console
psc -c panorama.xml dedup addresses --not-strict
```

`--not-strict` only widens what's *listed* — it grants no merge power. A pair it
surfaces (e.g. a host and its network) has different exact values, so
[`dedup merge`](#the-safety-gate) still refuses it unless you pass
`--allow-value-change`. Treat `--not-strict` as a discovery aid, then decide
case by case whether the masked-equal objects really should be merged.

## Merge two objects

`dedup merge` collapses one object (`--remove`) into another (`--keep`),
repointing **every** reference before deleting the loser:

```console
psc -c panorama.xml dedup merge --keep h-web1 --remove web-primary
```

By default this is a **dry-run**. You'll see the plan:

```
merge address 'web-primary'@shared -> 'h-web1'@shared
  • address-group 'grp-web' @shared static: ['h-web1', 'web-primary'] -> ['h-web1']
  • nat-rule 'nat-web' @shared pre source: ['web-primary'] -> ['h-web1']
  • delete address 'web-primary' @shared
dry-run — re-run with --apply to execute
```

### What gets rewritten

Every reference the [reference graph](references-and-audit.md) knows about:

- static address-group membership,
- security rule `source` / `destination`,
- NAT rule `source` / `destination` (translation fields are flagged for review),
- every other rulebase's `source` / `destination` / `service` / `tag` (PBF,
  decryption, authentication, QoS, application-override, DoS, SD-WAN,
  tunnel-inspect, network-packet-broker). A PBF next-hop object has no flat
  member list, so a merge that would strand it is **blocked** for manual review.

References are rewritten **before** the object is deleted, and duplicate members
are collapsed (a group that listed both names ends up with just the survivor).

### Merge a whole bucket at once

Pairwise `--keep/--remove` collapses two objects. When a value has *three or more*
duplicate names, `--group <value>` collapses the **entire bucket** toward one
survivor in a single plan — every non-survivor is repointed onto the survivor and
deleted:

```console
psc -c panorama.xml dedup merge --group 10.0.0.10/32                 # dry-run
psc -c panorama.xml dedup merge --group 10.0.0.10/32 --keep h-web1   # pick the survivor
psc -c panorama.xml dedup merge --group 10.0.0.10/32 --apply --out fixed.xml
```

- The value is a **bucket** from `dedup addresses` (run it first to see them).
- `--keep NAME` chooses the survivor; omit it and the **most visible** member wins
  — the one highest in the device-group hierarchy (`shared`, else the
  device-group nearest the root). Collapsing upward is what makes a duplicate
  disappear for every device-group at once. A member the other members' rules
  could never resolve — one in an unrelated device-group branch — is skipped
  however high it sits, since keeping it would only block the merge.
- `--group` and `--remove` are mutually exclusive.
- `--not-strict` matches the bucket under host-bit masking (see
  [Strict by default](#strict-by-default)).

The safety gate below applies unchanged — a bucket whose members differ in value
still needs `--allow-value-change`.

## The safety gate

`psc` **refuses** (exit `6`) a merge that would change meaning or that it can't
perform safely:

- **Different values.** Merging objects with different values changes what rules
  match. Blocked unless you pass `--allow-value-change`.
- **Invisible survivor.** If the kept object isn't visible where a reference
  lives (e.g. it's in a sibling device-group), the merge is blocked rather than
  creating a dangling reference.

```console
$ psc -c panorama.xml -o json dedup merge --keep net-10 --remove local-only --remove-location DG-EDGE
{"error": "plan blocked (unsafe): value mismatch: ...", "type": "conflict", ...}
$ echo $?
6
```

Visibility is judged against the config **as the plan leaves it**, not as it
stands now. PAN-OS resolves a name by walking upward only — the referrer's own
device-group, then its parent, and so on up to `shared`; a sibling device-group
is never consulted.

### Collapsing a device-group's local copy

The most common cleanup is a device-group holding its own copy of an object that
already exists in `shared` under the same name. The local copy *shadows* the
shared one, so every rule in that device-group binds to the local object today.
Merging them deletes the local copy and lets those rules re-resolve upward to the
survivor. No member list changes — the rules keep the same name — so the plan
carries **only the delete**, and `psc` warns about what silently moved:

```console
$ psc -c panorama.xml dedup merge --keep web --keep-location shared --remove web --remove-location DG-EDGE
  ! 2 reference(s) will re-resolve from 'web'@DG-EDGE to 'web'@shared (inheritance collapse)
  • delete address 'web' @DG-EDGE
```

This still blocks when an **intermediate** device-group sits on the upward walk
with an object of the same name. In `shared` → `DG-EMEA` → `DG-EDGE`, dropping
`'web'@DG-EDGE` in favour of `'web'@shared` makes `DG-EDGE` stop at
`'web'@DG-EMEA` — not the survivor. Merge the intermediate copy too (a `--group`
bucket merge collapses all of them in one plan), or the plan is refused.

### Attribute drift

The merge gate compares **values**. A dropped device-group copy can still carry
tags or a description the survivor lacks, and losing those changes what that
device-group sees. Tags are the sharp edge — they decide **dynamic address-group
membership**, so a dropped tag changes what traffic a DAG matches. `psc` warns
rather than blocks; the plan is yours to approve:

```console
  ! dropped 'web'@DG-EDGE has tags not on 'web'@shared: prod
  ! tag 'prod' is used by dynamic address-group 'dag-prod'@DG-EDGE — its membership will change
```

## Applying

=== "Offline (write a new file)"

    ```console
    psc -c panorama.xml dedup merge --keep h-web1 --remove web-primary \
        --apply --out fixed.xml
    ```

    `fixed.xml` is a complete, loadable config. The source export is untouched.

=== "PAN-OS set script"

    ```console
    psc -c panorama.xml -o set dedup merge --keep h-web1 --remove web-primary
    ```

    Member edits render as `delete` + `set`, so pasting them is idempotent
    (PAN-OS `set` on a member field appends; the leading `delete` makes it a
    true replace).

## Locations

By default both objects are taken from `--device-group` (or `shared`). Override
per object with `--keep-location` / `--remove-location`.

## Promote a cross-DG duplicate into `shared`

`dedup merge` needs a survivor to collapse onto. The most common Panorama
cleanup finds a duplicate that has **no** survivor to pick: the same object
defined independently in `DG-A` and `DG-B`, and nowhere in `shared` or a common
ancestor. There is nothing above the device-groups to merge into — `dedup
merge` structurally cannot fix this one.

`dedup promote` is the missing operation. It **creates** the object once at the
destination (`shared` by default, or `--to <ancestor-DG>`) and **deletes** every
device-group copy. Because promotion only ever moves upward, every reference
falls through to the new definition by ordinary PAN-OS shadowing — the plan
never needs to repoint a single reference:

```console
$ psc -c panorama.xml dedup promote address --group 10.0.0.1/32
promote 2 address(s) -> @shared
  • create address 'web' @shared
  • delete address 'web' @DG-A
  • delete address 'web' @DG-B
dry-run — re-run with --apply to execute
```

```console
$ psc -c panorama.xml -o set dedup promote address --group 10.0.0.1/32
# promote 2 address(s) -> @shared
set shared address web ip-netmask 10.0.0.1/32
delete device-group DG-A address web
delete device-group DG-B address web
```

### Selecting the bucket

- `--group <value>` (address/service) selects a duplicate-*value* bucket, the
  same values `dedup addresses`/`dedup services` list.
- `--name <name>` (address-group **or tag**) selects every object with that
  *name* — both kinds are name-keyed. For address-groups there's no way to type
  an effective-leaf-set selector, and their members' effective sets must match
  or the plan is blocked (the same equivalence check `dedup groups` uses). Tags
  have no value at all, so a same-named pair is always one bucket (see
  [Duplicate tags](#duplicate-tags)).
- `--all` sweeps every promotable bucket of the kind in one plan. Buckets it
  cannot promote are **skipped and reported on stderr**, never silently
  dropped:

  ```console
  $ psc -c panorama.xml dedup promote address --all
  note skipped 1 bucket(s):
    - ip-netmask 10.0.0.10/32: bucket names diverge ('h-web1'@DG-A, 'web-primary'@DG-B); pass --keep NAME to unify them on one name
  promote all duplicate address buckets -> @shared
  ```

  `--all` and `--keep` are mutually exclusive — one survivor name cannot span
  every bucket in the sweep. Promote a divergent bucket on its own with
  `--group`/`--name` and `--keep` instead.

### Divergently named copies: `--keep`

When the device-group copies were named differently (`h-web1`@`DG-A` vs.
`web-primary`@`DG-B`), promote has no way to choose a survivor name on its own
— `--keep NAME` picks it, and every reference to the *other* names is
repointed onto it before their copies are deleted:

```console
$ psc -c panorama.xml dedup promote address --group 10.0.0.1/32 --keep h-web1
promote 2 address(s) -> @shared
  • create address 'h-web1' @shared
  • security-rule 'allow-web-primary' @DG-B pre source: ['web-primary'] -> ['h-web1']
  • delete address 'h-web1' @DG-A
  • delete address 'web-primary' @DG-B
dry-run — re-run with --apply to execute
```

Without `--keep`, a divergently-named bucket is a **blocker** (exit `6`) rather
than a guess at which name should win.

### `--cascade` for address-groups

`--cascade` also promotes a group bucket's transitive dependencies (its
members, their tags) to the same destination, deepest-first, in one plan —
without it, a dependency not already visible at the destination blocks the
promotion and is listed to promote first. This is the same cascade `move`
offers for a single object, extended to a whole bucket: every device-group's
copy of a shared dependency folds onto **one** upsert at the destination
(sources still lose their own copy), and a dependency still needed by an
object left behind is promoted but its source copy is retained (with a
warning).

### Safety

The same gates as `move`/`merge` apply, run once per bucket member: a
destination that isn't `shared` or a common ancestor of every member is
rejected outright, an intermediate device-group already defining the name (a
shadow) blocks, and a dependency invisible at the destination blocks unless
`--cascade` is passed. Promote additionally requires every member to carry the
same value (or, for groups, the same effective leaf-address set) — there's no
`--allow-value-change` escape hatch, since promote never re-derives a
canonical value. A sibling device-group that keeps defining the same name after
the promotion is not a blocker — the plan still repoints the members it knows
about — but it **warns**, since that sibling will keep shadowing the promoted
object for its own subtree.

Applies the same way as merge: dry-run by default, `--apply --out FILE`
offline (never overwriting the source export), `--apply` alone on a live
profile (candidate config, never committed), and `-o set`/`-of set` for the
PAN-OS script.

## Duplicate tags

A tag defined under the **same name in more than one location** (e.g. `prod` in
`shared`, `DG-A`, and `DG-B`) is a redundant definition of one logical tag.
PAN-OS resolves a tag reference by *name* up the device-group chain, so those
copies all stand in for the same binding. `dedup tags` lists them:

```console
psc -c panorama.xml dedup tags
```

Tags are **name-keyed**, not value-keyed: a tag carries no match-affecting value
(its `color`/`comments` are cosmetic), so a same-named pair is always one bucket.
Consolidate a bucket with `dedup promote tag`:

```console
$ psc -c panorama.xml dedup promote tag --name prod
promote 2 tag(s) -> @shared
  • create tag 'prod' @shared
  • delete tag 'prod' @DG-A
  • delete tag 'prod' @DG-B
dry-run — re-run with --apply to execute
```

Because the name is stable, the consolidation repoints **nothing** — deleting
the device-group copies simply lets their references (objects, rules, and
dynamic-address-group filters) re-resolve upward to the survivor by name. This
makes tag consolidation strictly safer than address/service promote, which can
require repoints when copies are named differently.

The one thing to watch is cosmetic: if the discarded copies carry a different
`color` or `comments` than the survivor, the survivor's win, and `psc` **warns**
(it never blocks on this — the operator decides):

```console
$ psc -c panorama.xml dedup promote tag --name prod
...
note 'prod'@DG-B has color color5 the promoted copy will not carry (survivor keeps color1)
```

`--all` sweeps every duplicate-tag bucket in one plan. `--group`, `--keep`, and
`--cascade` do **not** apply to `tag`: tags aren't value-keyed (no `--group`),
a bucket has a single name (no `--keep`), and a tag is a dependency leaf with no
member closure to cascade. Consolidation is available in the workbench dedup
spoke too — select same-named tags and pick a promote destination.

## Duplicate address-groups

`dedup addresses`/`services` finds duplicate *objects*; `dedup groups` finds
duplicate **address-groups** — two groups that resolve to the *same effective
set of leaf addresses*:

```console
psc -c panorama.xml dedup groups
```

Groups are bucketed by the canonical set of hosts they expand to (nested groups
are flattened first), so two groups land in the same bucket even if their names
and direct members differ, as long as they ultimately reach the same addresses.

```json
{
  "kind": "address-group",
  "value": "{ip-netmask:10.0.0.1/32, ip-netmask:10.0.0.2/32}",
  "members": [
    {"name": "grp-a", "location": "shared"},
    {"name": "grp-b", "location": "shared"}
  ]
}
```

The audit is **not exhaustive**: dynamic (filter-based) groups are runtime-only,
and groups with dangling/malformed members can't be resolved — both are excluded
and counted on stderr (`note audit is not exhaustive: skipped N dynamic and M
unresolvable group(s)`). Scope the comparison with `--location` (default: the
global `-d/--device-group` if set, else compare across all locations).

## Merge two address-groups

`dedup merge-group` collapses one group into another, reusing the same
repoint-before-delete engine as object merge:

```console
psc -c panorama.xml dedup merge-group --keep grp-a --remove grp-b
psc -c panorama.xml dedup merge-group --keep grp-a --remove grp-b --apply --out fixed.xml
```

- `--keep` (required): the survivor group.
- `--remove` (required): the group collapsed into `--keep` and deleted.
- `--location` sets both; `--keep-location`/`--remove-location` override per
  group (default: the global `-d/--device-group`, else `shared`).
- plus `--apply`, `--out`, `-of/--output-format`.

Every referrer of `--remove` is repointed onto `--keep` *before* the dropped
group is deleted. Unlike object merge, there is **no value-change override** —
the merge is **refused** (exit `6`) unless the two groups expand to the *same*
effective member set, because collapsing groups that mean different things would
silently change rule matching. It also blocks on a **nested or cyclic** pair
(one group already contains the other) and when the **survivor isn't visible**
where a reference lives.
