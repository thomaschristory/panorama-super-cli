# Comparing and porting configs

Two read-oriented workflows for moving between configs: **`diff`** (what changed
between two configs, or two device-groups) and **`export` / `set -f`** (dump
objects as NDJSON and re-import them elsewhere).

## `diff` — what changed?

`diff` is a **pure read** drift report. It exits `0` even when the two sides
differ — a difference is *data*, not an error.

### Two configs

The pre/post-change review: export before and after a change (or from two
Panoramas) and compare.

```console
psc diff before.xml after.xml
```

It reports added / removed / changed objects, groups, and rules, grouped by kind
(address, address-group, service, service-group, tag, security-rule, nat-rule).
A `changed` row summarizes the differing fields as `field: before -> after`.

### Two device-groups

Compare the *effective visible object sets* of two device-groups within the
single loaded config:

```console
psc -c panorama.xml diff --device-group DG-EDGE --against DG-CORE
```

The two modes are mutually exclusive — pass two config paths **or**
`--device-group A --against B`, not both.

```console
psc diff before.xml after.xml -o json | jq '.addresses.changed'
```

## Porting objects as NDJSON {: #export-import }

`export` dumps every object of one kind as **NDJSON** — one JSON object per line,
each the canonical model, ordered by `(location, name)` for stable, diff-friendly
output.

```console
psc -c source.xml export addresses > addresses.ndjson
psc -c source.xml export services --out services.ndjson
```

Kinds: `addresses`, `address-groups`, `services`, `service-groups`, `tags`.
Output goes to stdout, or to `--out <file>` (a plain artifact write, never a
mutation). The global `-d/--device-group` scopes the export like any read.

The read-side counterpart is **`set <kind> -f <file>`**: feed the NDJSON straight
into another config as a **bulk import**.

```console
psc -c target.xml set address -f addresses.ndjson               # dry-run plan
psc -c target.xml set address -f addresses.ndjson --apply --out merged.xml
```

The `set` subcommand is the **singular** kind (`address`, `address-group`,
`service`, `service-group`, `tag`) — each accepts `-f`, importing an NDJSON file
of that kind. Import plans the *whole batch* as one reviewable `ChangeSet` — the same
[`set` validation](editing-objects.md#create-or-update-an-object) applied to every
line and aggregated. It flows through the identical dry-run-default + `--apply`
gate, and **one blocker refuses the whole file** (import never writes objects
directly). In import mode the singular flags (`--name`, `--type`, …) are ignored;
everything comes from the file.

```console
# Copy shared services from one config to another, reviewing the plan first.
psc -c prod.xml export services --out svc.ndjson
psc -c staging.xml set service -f svc.ndjson -o json | jq -e '.blockers | length == 0'
psc -c staging.xml set service -f svc.ndjson --apply --out staging-with-svc.xml
```
