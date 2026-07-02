# Editing objects

The find/dedup/refs commands *read* and the merge/rename commands *reshape* the
config. This guide covers the three commands that **create, modify, and remove**
objects outright:

- [`psc set …`](#create-or-update-an-object) — create or update one object.
- [`psc rule edit-member`](#edit-one-rule-field-member) — idempotently add or
  remove one member of a rule field.
- [`psc decommission`](#decommission-an-address) — reference-safe teardown of
  the address objects matching an IP/CIDR.

All three are **dry-run by default** and share the same write surface as the
rest of `psc`: `--apply` executes, `--out PATH` writes a reviewable artifact,
and `-of/--output-format xml|set` chooses what that artifact holds. See
[Writes and safety](safety.md) for the full contract.

## Create or update an object

`psc set` writes a single object with PAN-OS-accurate validation, one
subcommand per kind:

```console
psc -c panorama.xml set address --name h-web1 --type ip-netmask --value 10.0.0.10/32
psc -c panorama.xml set address-group --name grp-web --member h-web1 --member h-web2
psc -c panorama.xml set address-group --name dag-prod --filter "'prod' and 'web'"
psc -c panorama.xml set service --name tcp-8443 --protocol tcp --dest-port 8443
psc -c panorama.xml set service-group --name svc-web --member tcp-443 --member tcp-8443
psc -c panorama.xml set tag --name prod --color color5 --comments "production"
```

Common flags on every subcommand: `--name` (required), `--location`
(default: the global `-d/--device-group`, else `shared`), `--apply`, `--out`,
`-of/--output-format`. Tags via `--tag` (repeatable) where the kind supports
them.

Kind-specific flags:

| Subcommand | Required | Notable |
| --- | --- | --- |
| `address` | `--name`, `--type`, `--value` | `--type` is `ip-netmask` \| `ip-range` \| `ip-wildcard` \| `fqdn`; `--value` stored verbatim; `--description`, `--tag` |
| `address-group` | `--name` | exactly one of `--member` (repeatable, static) **or** `--filter` (dynamic tag expression); `--description`, `--tag` |
| `service` | `--name`, `--protocol`, `--dest-port` | `--protocol` is `tcp` \| `udp`; `--dest-port` required (PAN-OS mandates a destination port), `--source-port` optional; `--description`, `--tag` |
| `service-group` | `--name`, `--member` | `--member` repeatable (≥1); `--tag` |
| `tag` | `--name` | `--color` is `color1`..`color42`; `--comments` (a tag has no `--description`) |

### What gets validated

Validation mirrors what PAN-OS would reject, so the failure happens on your
laptop instead of on commit. Validation errors exit `4` (`validation`):

- **Names** must be ≤63 characters (≤127 for a tag) and start with an
  alphanumeric.
- **Descriptions** ≤255 characters.
- **Address** must carry exactly one value kind (one `--type`/`--value`).
- **Service** needs a destination port (or a source port).
- **Tag color** must be `color1`..`color42`.

Two failures are **blockers** (exit `6`, `conflict`) rather than plain
validation errors, because they would silently break references:

- **Cross-kind name collision** — a name already used by a *different* kind at
  that location (e.g. setting an address named like an existing service-group).
- **In-place type/mode change on update** — changing an address's value type, or
  flipping an address-group between static (`--member`) and dynamic
  (`--filter`), on an object that already exists. Delete and recreate instead.

### Create vs update, live vs offline

`set` creates a new object or updates an existing one. Live `--apply` **only
creates** — updating an existing object over the XML API is refused (`config`).
To update, use **offline** `--apply`:

```console
psc -c panorama.xml set address --name h-web1 --type ip-netmask --value 10.0.0.11/32 \
    --apply --out updated.xml
```

This keeps the risky operation (rewriting a live object) on a reviewable file
rather than mutating the device candidate blind.

### Bulk import from NDJSON

Every `set` subcommand also takes `-f/--file <objs.ndjson>` — a bulk import of
many objects of that kind, as emitted by [`export`](comparing-and-porting.md):

```console
psc -c target.xml set address -f addresses.ndjson               # dry-run plan
psc -c target.xml set address -f addresses.ndjson --apply --out merged.xml
```

The whole file is planned as **one** reviewable `ChangeSet` (the same per-object
validation, aggregated); the singular flags are ignored, and **one blocker refuses
the whole file**. See
[Comparing and porting configs](comparing-and-porting.md#export-import).

## Edit one rule-field member

`psc rule edit-member` adds or removes **one** member of **one** rule field,
idempotently:

```console
psc -c panorama.xml rule edit-member --rule allow-web --field source --add h-web1
psc -c panorama.xml rule edit-member --rule allow-web --field source --remove h-old
psc -c panorama.xml rule edit-member --rule allow-web --field service --add tcp-8443 --rulebase post
```

- `--rule` (required): the rule name to edit.
- `--field` (required): `source` \| `destination` \| `service` \| `application`.
- exactly one of `--add` / `--remove`.
- `--rulebase`: `pre` \| `post` (default `pre`).
- `--location`: `shared` or a device-group (default: the global
  `-d/--device-group`, else `shared`).
- plus `--apply`, `--out`, `-of/--output-format`.

### Why removal renders delete-then-set

PAN-OS `set … <field> [ member ]` **appends** to the existing list rather than
replacing it. So a removal can't be expressed as a single `set`: `psc` renders a
**delete of the whole field** followed by a **re-set of the remaining list**.
The upshot is that any edit is idempotent — re-running `--add h-web1` when
`h-web1` is already present (or `--remove` when it's already absent) is a no-op
that changes nothing.

### Validation and limits

- **NAT `service` is scalar** — a NAT rule's service is a single value, not a
  list, so member-editing it is **blocked**.
- `application` only exists on security rules; `--field application` against a
  NAT/policy rule is a validation error.
- An **unknown rule** exits `5` (`not_found`); an **ambiguous** rule (matching
  in more than one place) exits `4` (`validation`) — disambiguate with
  `--rulebase`/`--location`.

## Decommission an address

`psc decommission` performs a **reference-safe, ordered teardown** of every
address object that matches one or more IPs/CIDRs/ranges. It's the safe inverse
of `set`: instead of hand-scrubbing groups and rules before deleting an object,
you name the IP and let `psc` plan the cascade.

```console
psc -c panorama.xml decommission 10.1.0.5
psc -c panorama.xml decommission 10.1.0.0/24 10.2.0.0/24
psc -c panorama.xml decommission --file retired-ips.txt
psc -c panorama.xml decommission 10.1.0.5 --scope DG-EDGE
```

Targets come from positional arguments, repeated `--target`, and/or a
`-f/--file` list (one per line, `#` comments). `--scope DG` limits the object
search to one device-group (plus inherited `shared`); the default is the global
`-d/--device-group`, else everywhere.

### The teardown order

The plan cascades to a **fixpoint** in this order:

1. **Scrub** the matched objects from every address-group member list.
2. **Scrub** them from every rule `source`/`destination`.
3. **Delete orphaned rules** — a rule whose `source` *or* `destination` became
   empty (an `any` field survives; it was never narrowed by this object).
4. **Delete emptied groups** — a group left with no members.
5. **Delete the objects** themselves.

Because deleting an emptied group can orphan *its* referrers, the plan repeats
until nothing more changes. Example dry-run plan:

```
decommission address objects
  ! orphan rule 'r-sole-source' @shared pre will be deleted (source/destination empty after decommission — verify no traffic depends on it)
  • address-group 'g-mixed' @shared static: ['h-dead', 'h-keep'] -> ['h-keep']
  • security-rule 'r-sole-source' @shared pre source: ['h-dead'] -> []
  • delete security-rule rule 'r-sole-source' @shared pre
  • delete address 'h-dead' @shared
  • delete address-group 'g-dead-only' @shared
dry-run — re-run with --apply to execute
```

### Only exact and within matches are torn down

`decommission` removes only objects that **equal** the target or are **inside**
it (an `EXACT` or `WITHIN` match). A *broader* object that merely *contains* the
target is left in place — decommissioning `10.1.0.5` never deletes the supernet
`10.1.0.0/24`, because that network is still meaningful for other hosts.

### Keep groups / keep rules

- `--keep-groups` scrubs the matched objects from group and rule member fields
  but deletes **neither** the groups **nor** the objects — useful when you want
  to vacate the references but retain the now-empty shells.
- `--keep-rules` keeps rules that would otherwise be deleted as orphans (empty
  `source`/`destination`), leaving them for manual review.

### Blockers and warnings

- **Blockers** (exit `6`, refuse to apply): a matched object referenced by a
  **NAT translation** field or a **PBF forwarding next-hop** (neither is a flat
  member list `psc` can safely rewrite), or matched by a **DAG filter tag**
  (dynamic membership psc can't enumerate). Resolve the reference by hand, then
  re-run.
- **Warnings** (surfaced, don't block): every orphan-rule deletion is a warning
  — verify no traffic depends on the rule before you `--apply`.

### Applying

Like every write, `decommission` is dry-run until `--apply`, and offline
`--apply` needs `--out`:

```console
psc -c panorama.xml decommission 10.1.0.5 --apply --out torn-down.xml
psc -c panorama.xml decommission 10.1.0.5 --out torn-down.set -of set   # review the script first
```
