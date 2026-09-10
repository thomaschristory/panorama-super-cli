# Writes and safety

`psc` is built so that the *default* behaviour is safe and the dangerous thing
is always explicit.

## Dry-run by default

Every mutating command (`dedup merge`, `dedup merge-group`, `dedup promote`,
`name rename`, `name apply`, `set …` including bulk `set -f`, `rule edit-member`,
`decommission`, `delete`, `move`) **prints a plan and exits without changing anything**
unless you pass `--apply`. The workbench stages plans and applies them as one
batch on `ctrl+a`, under the same gate. The plan
you see is the exact change-set that `--apply` would execute — there's no
second, hidden code path.

```console
psc -c panorama.xml dedup merge --keep h-web1 --remove web-primary   # dry-run
psc -c panorama.xml dedup merge --keep h-web1 --remove web-primary --apply --out fixed.xml
```

## Repoint before delete

A merge or rename never deletes or renames an object until every reference to it
has been rewritten — across `shared` and every device-group, in groups and every
object-referencing rulebase (security, NAT, and PBF, decryption, authentication,
QoS, application-override, DoS, SD-WAN, tunnel-inspect, network-packet-broker).
The change-set is ordered: upserts → reference rewrites → renames → deletes.

A delete gets the same ordering. `decommission` and `delete` scrub every
reference before they remove the referent, and they refuse the plan when a
reference cannot be rewritten. The change-set is ordered: reference rewrites →
rule deletions → object deletions.

This guarantee covers the reference sites psc **scans**. Some legitimate
reference sites are *not* scanned (templates, network/device config, NAT-rule
tags, and more). A delete driven by `refs unused` is therefore safe against the
references psc **sees**, and blind to the rest. Read
**[Coverage and blind spots](coverage-and-limitations.md)** before deleting
anything, especially `shared` objects.

## Blockers are a hard gate

A change-set carries a `blockers` list. If it's non-empty, the plan is **unsafe**
and `psc` refuses to apply it — even with `--apply` — exiting `6` (`conflict`).
Blockers are raised instead of doing something surprising. Examples:

- merging objects with different values (changes what rules match),
- merging address-groups that expand to different effective member sets, or a
  nested/cyclic group pair,
- a reference that can't be repointed because the survivor isn't visible there,
- a rename that would shadow a same-named object in another scope,
- a `set` whose name collides with a *different* kind, or that would change an
  existing object's value type or static/dynamic mode in place,
- a `decommission` target referenced by a NAT translation field or a PBF
  forwarding next-hop, or matched by a dynamic-address-group filter tag,
- a `delete` target that names no object at that location, or whose references
  psc cannot rewrite (see [below](#reference-safe-deletion-by-name)).

Warnings (e.g. a NAT translation field that needs manual review, or an
orphan-rule deletion during `decommission`) are surfaced but don't block.

## Ordered, cascading teardown

[`decommission`](editing-objects.md#decommission-an-address) is the safe inverse
of a hand-rolled delete: rather than scrubbing groups and rules yourself before
removing an address, you name the IP/CIDR and `psc` plans the whole cascade in a
fixed, reference-safe order — scrub groups → scrub rules → delete orphaned rules
(empty `source`/`destination`; `any` survives) → delete emptied groups → delete
the objects — repeating to a fixpoint so that deleting an emptied group also
repoints or orphans *its* referrers. Only objects that **equal** or fall
**within** a target are torn down; a broader containing object is left in place.
Like every write it is dry-run until `--apply`, and the blocker gate above
applies before any change is made.

### A shadowed name falls through

PAN-OS resolves a bare name up the device-group chain. A device-group object
therefore hides a `shared` object of the same name. A delete of the device-group
object does not break a reference to that name: the reference falls through to
the `shared` object.

`decommission` and `delete` ask this question before they touch a reference.
They scrub a name only when the name resolves to nothing after the plan applies.
A name that still resolves stays in the group member list or the rule field. The
group therefore does not become empty, and the rule is not orphaned.

The referrer keeps the name, but the name can point to another object with
another value. Each plan prints one warning for each reference that falls
through. The warning gives the referrer, the name, and the surviving object with
its value:

```text
address-group 'g'@dg-a static keeps the name 'web'; after this plan the name
points to address 'web'@shared (10.0.0.1/32) — make sure that the referrer is
still correct
```

Read each warning before you `--apply`. To remove the name completely, put the
`shared` object in the same run.

## Reference-safe deletion by name

[`delete`](../reference/cli.md#delete) applies that same teardown to objects you
name instead of to an IP. It is the sink for a verified
[`refs unused`](references-and-audit.md#from-unused-to-deleted) list, and it
covers all five `unused` kinds: `address`, `address-group`, `service`,
`service-group` and `tag`.

It obeys the same four safety rules as the rest of `psc`:

- **Dry-run by default.** `delete` prints the plan and changes nothing until you
  pass `--apply`.
- **Blockers are a hard gate.** A blocked plan carries **zero** operations and
  exits `6`. It never deletes "the safe part" of a list.
- **Scrub before delete.** Every group member list and rule field that names the
  object is rewritten before the object goes.
- **Offline `--apply` writes to `--out`.** Your export is never overwritten.

`--keep-groups` scrubs the member lists but deletes neither the groups nor the
objects, so nothing cascades. `--keep-rules` keeps a rule that loses a required
field, and warns instead of deleting it.

### What `delete` blocks on

`plan_purge` raises a blocker rather than doing something surprising:

- a target that names **no object** at that location — a delete list must not
  silently drop an entry,
- a kind that is not deletable (anything outside the five kinds above),
- a **NAT source- or destination-translation** field that names the object,
- a **PBF forwarding next-hop** that names the object,
- a **surviving dynamic address-group** whose filter selects the object by tag —
  psc cannot edit a filter expression, so removing the address, or the tag
  itself, would change what the group matches,
- a **tag** that a surviving object still carries — psc has no repoint path for
  an object's own tag list.

Fix the cause by hand (edit the NAT rule, the next-hop, the filter clause, or
the tag list), or add the referring object to the same `delete` run, then
re-run. A dynamic address-group that the *same* plan deletes does not block.

### What `delete` warns about

Warnings are surfaced in the plan and never block. Read them before you
`--apply`:

- an **orphan rule** will be deleted, because the scrub emptied its `source`,
  `destination`, `service` or `application` (`any` is a real surviving member,
  so only a field that holds nothing at all counts as empty),
- a rule kept by `--keep-rules` now has an empty required field, so it can no
  longer match traffic — review it by hand,
- a group kept by `--keep-groups` is now empty and may dangle,
- a candidate is a **`shared`** object — verify that no device-group, template or
  device config outside this export depends on it,
- a candidate **carries a tag**, so a dynamic address-group may select it at
  runtime through an externally registered IP; verify it in Panorama first,
- a reference keeps a name that **falls through** to another object, because
  this delete only removes the shadow (see
  [A shadowed name falls through](#a-shadowed-name-falls-through)).

## Offline apply never overwrites your export

Offline, `--out PATH` writes the rewritten config there — it will refuse to
write back over the source file. Your export stays pristine and the change is
reviewable as a diff. `--out` is an artifact request, so it is honoured even in
a dry-run (writing a file is not a mutation); `--apply` alone still requires
`--out`.

```console
psc -c panorama.xml dedup merge --keep a --remove b --apply --out fixed.xml
diff <(xmllint --format panorama.xml) <(xmllint --format fixed.xml)
```

By default the `--out` file is the rewritten config XML. Pass
`-of/--output-format set` to instead write the equivalent PAN-OS `set` script
(the creates/deletes/repoints that achieve the same change) — easier to read and
to paste into a config session. The blocker gate and repoint-before-delete
ordering apply to both formats identically; a blocked plan writes no file.

```console
psc -c panorama.xml dedup merge --keep a --remove b --apply --out plan.set -of set
```

## Live writes

Live `--apply` pushes the plan to Panorama's **candidate** config over the XML
API and **never commits** — `psc` leaves a candidate for you to review and
commit yourself, just as offline leaves a `--out` file.

```console
psc -p prod dedup merge --keep h-web1 --remove web-primary          # dry-run
psc -p prod dedup merge --keep h-web1 --remove web-primary --apply  # writes the candidate
```

The same safety contract holds on the wire: `blockers` refuse the apply before
any device contact, and references are repointed *before* the object is deleted.
A name carrying a single quote can't be addressed by an XML-API xpath, so it's
rejected up front (`input`, exit `2`) rather than sent malformed — rename it or
apply that plan offline. If a write fails mid-plan, `psc` reports how far it got
and leaves the uncommitted candidate for you to inspect or revert.

Prefer to stage the change as a file instead? Add `--out plan.set -of set` (it
writes the artifact without pushing, even on a live profile), or print the
script to stdout with `-o set` (paste / `load config partial`).

## Debugging

`--debug` streams structured logs to **stderr**; stdout stays clean for pipes.
