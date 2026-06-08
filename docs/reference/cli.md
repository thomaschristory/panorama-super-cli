# CLI reference

Run `psc --help`, or `psc <group> --help`, for the authoritative, version-exact
surface. This page is an overview.

## Global options

These are **context** options — pass them *before* the subcommand:

| Option | Description |
| --- | --- |
| `-c, --config PATH` | Offline: exported Panorama config XML. |
| `-p, --profile NAME` | Live: profile from `~/.psc/config.yaml`. |
| `-o, --output FMT` | `table`, `json`, `jsonl`, `yaml`, `csv`, `set`. |
| `-d, --device-group NAME` | Scope to one device-group (plus shared). |
| `--strict` | Exit non-zero when a lookup finds nothing / finds problems. |
| `--debug` | Verbose structured logs on stderr. |
| `--version` | Print version and exit. |

Write-execution options (`--apply`, `--out`, `-of/--output-format`) belong to
the individual mutating commands and are passed *after* the command.

- `--out PATH` writes a reviewable artifact file; `-of xml|set` chooses what it
  holds (default `xml`). It's an artifact request, not a mutation, so it's
  honoured even in a dry-run and on a live profile — writing a file never
  touches the source export or the device candidate.
- `--apply` executes the change against the managed config: **live** pushes the
  plan to Panorama's candidate config and never commits; **offline** requires
  `--out PATH` (the rewritten file *is* the execution, and never overwrites the
  source export).

See [Writes and safety](../guides/safety.md).

## Commands

### find

```
psc find ip <target>... [-f FILE]
psc find object <name>
```

Resolve an IP/CIDR/range/FQDN (or a file of them) to objects; or locate an
object by exact name. See [Finding objects](../guides/finding-objects.md).

### dedup

```
psc dedup addresses [--not-strict]
psc dedup services
psc dedup groups [--location LOC]
psc dedup merge --keep NAME --remove NAME [--location LOC]
               [--keep-location LOC] [--remove-location LOC]
               [--allow-value-change] [--apply] [--out PATH] [-of xml|set]
psc dedup merge-group --keep NAME --remove NAME [--location LOC]
               [--keep-location LOC] [--remove-location LOC]
               [--apply] [--out PATH] [-of xml|set]
```

Find duplicate objects (`addresses`/`services`) or address-groups with identical
effective member sets (`groups`); merge one object (`merge`) or address-group
(`merge-group`) into another, repointing all references. `merge-group` has **no**
value-change override — it refuses unless the groups expand to the same set. See
[Duplicates and merging](../guides/duplicates-and-merging.md).

### audit

```
psc audit overlaps
```

Report address objects whose IP ranges contain or overlap one another
(`ip-netmask`/`ip-range` only). Pure read; honours the global `-d/--device-group`
scope and `--strict` (exit `5` when nothing overlaps). See
[References and audit](../guides/references-and-audit.md#overlapping-and-contained-ranges).

### refs

```
psc refs used <name> [--kind KIND] [--location LOC]
psc refs unused [--kind KIND]
psc refs dangling
```

Where-used, recursive unused, and dangling-reference audit. See
[References and audit](../guides/references-and-audit.md).

### name

```
psc name lint [--all]
psc name rename --object OLD --to NEW [--kind KIND] [--location LOC] [--apply] [--out PATH] [-of xml|set]
psc name apply  --object NAME            [--location LOC] [--apply] [--out PATH] [-of xml|set]
```

Opt-in naming-template lint and reference-aware rename. See
[Naming templates](../guides/naming.md).

### set

```
psc set address       --name N --type ip-netmask|ip-range|ip-wildcard|fqdn --value V
                      [--description D] [--tag T]... [--location LOC] [--apply] [--out PATH] [-of xml|set]
psc set address-group --name N (--member M... | --filter EXPR)
                      [--description D] [--tag T]... [--location LOC] [--apply] [--out PATH] [-of xml|set]
psc set service       --name N --protocol tcp|udp --dest-port P [--source-port P]
                      [--description D] [--tag T]... [--location LOC] [--apply] [--out PATH] [-of xml|set]
psc set service-group --name N --member M... [--tag T]... [--location LOC] [--apply] [--out PATH] [-of xml|set]
psc set tag           --name N [--color color1..color42] [--comments C]
                      [--location LOC] [--apply] [--out PATH] [-of xml|set]
```

Create or update a single object with PAN-OS validation. `address` needs exactly
one `--type`/`--value`; `address-group` needs exactly one of `--member`/`--filter`;
`service` requires `--dest-port` (PAN-OS mandates a destination port); `--source-port` is optional. Validation errors
exit `4`; a cross-kind name collision or an in-place type/mode change on update is
a blocker (exit `6`). Live `--apply` only **creates** — updating an existing
object live is refused; use offline `--apply --out`. See
[Editing objects](../guides/editing-objects.md#create-or-update-an-object).

### rule

```
psc rule edit-member --rule R --field source|destination|service|application
                     (--add M | --remove M) [--rulebase pre|post] [--location LOC]
                     [--apply] [--out PATH] [-of xml|set]
```

Idempotently add or remove one member of a rule field (`--rulebase` default
`pre`). Removal renders a delete-of-field plus a re-set of the remaining list
(PAN-OS `set` on a member field appends). NAT `service` is scalar and is blocked;
`application` on a non-security rule is a validation error. An unknown rule exits
`5`, an ambiguous rule exits `4`. See
[Editing objects](../guides/editing-objects.md#edit-one-rule-field-member).

### decommission

```
psc decommission <ip|cidr|range>... [--target T]... [-f FILE] [--scope DG]
                 [--keep-groups] [--keep-rules] [--apply] [--out PATH] [-of xml|set]
```

Reference-safe, cascading teardown of the address objects matching an
IP/CIDR/range (or a `-f/--file` list): scrub from groups → scrub from rules →
delete orphaned rules (empty `source`/`destination`; `any` survives) → delete
emptied groups → delete the objects, repeating to a fixpoint. Only exact and
within matches are torn down (a broader containing object is left in place).
`--keep-groups`/`--keep-rules` stop short of deleting those. Blocks on
NAT-translation/PBF-next-hop references and DAG-filter-tag matches; orphan-rule
deletions are warnings. See
[Editing objects](../guides/editing-objects.md#decommission-an-address).

### move

```
psc move <address|address-group|service|service-group|tag> <name>
         --from <shared|DG> --to <shared|DG> [--apply] [--out PATH] [-of xml|set]
```

Promote one object from a device-group toward `shared` (create at the
destination, delete at the source). `--to` must be `shared` or an *ancestor* of
`--from` — the only direction in which references fall through to the
destination with no repoint. Blocks (exit `6`) on a sibling/child/unrelated
destination, an intermediate device-group that already defines the name (a
shadow), the object's own dependencies (members/tags) not being visible at the
destination, or a collision with a different-valued object already there. An
identical-valued collision drops the source copy. Single object per run;
dry-run by default.

### init

```
psc init [--name N] [--host H] [--port P] [--device-group DG] \
         [--user U | --api-key K] [--no-verify] [--insecure] [--default/--no-default]
```

Interactively bootstrap the first live profile. With `--user` (or an
interactive prompt) it exchanges a username/password for an API key via the
PAN-OS keygen API, runs a pre-flight probe, and writes a `0600` config. Pass
`--api-key` to store a key you already have instead of generating one. The
password is read from `$PSC_PASSWORD` or a hidden prompt — never a flag.

TLS certificates are verified by default (the keygen request carries the
password). For a self-signed Panorama, pass `--insecure` — it is recorded as the
profile's `verify_ssl: false` and reused by later live commands. `--no-verify`
is unrelated: it skips the *reachability* probe, not certificate checking.

### login

```
psc login [--name N] [--user U]
```

Verify a stored profile's API key with a `show system info` probe (selects the
profile from `--name`, then `--profile`, then the default). With `--user` it
re-generates (rotates) the key first and only persists it once the probe
succeeds. Auth failures exit `8`, unreachable/transport failures exit `7`.

### profile

```
psc profile list
psc profile add --name N --host H [--api-key K] [--port P] [--device-group DG] [--default]
psc profile remove <name>
```

Manage live connection profiles. `init`/`login` are the friendlier front door;
`profile add` is the scriptable, non-interactive form. `profile list` also
prints the config file's location (on stderr, so machine output stays clean) —
handy because the path is platform-dependent. See [Configuration](config.md).

### version

```
psc version
psc version check
```

`psc version` prints the installed version (the format-aware equivalent of the
`--version` flag). `psc version check` queries PyPI and reports whether a newer
release is available; it exits 0 either way and emits a typed `transport` error
if PyPI is unreachable.
