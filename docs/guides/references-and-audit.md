# References and audit

`psc` builds a **reference graph** over the whole config: who points at whom,
across `shared` and every device-group, honouring PAN-OS name resolution
(a device-group-local object shadows a same-named `shared` one).

## Where is this object used?

The pre-flight for any delete or rename:

```console
psc -c panorama.xml refs used h-web1
```

Lists every reference that *resolves* to that object — group memberships,
security rule `source`/`destination`, NAT `source`/`destination`/translation,
and the `source`/`destination`/`service`/`tag` fields of every other rulebase
(PBF, decryption, authentication, QoS, application-override, DoS, SD-WAN,
tunnel-inspect, network-packet-broker), plus a PBF forwarding next-hop object.

Each row describes the *referrer*, not the object you trace. The `tags` column
shows the tags of that referrer. The referrer is the rule or the group that
points at the object. Table and CSV output join the tags with commas. JSON,
JSONL and YAML output carry a real list. An untagged referrer shows an empty
list. A rule tag often records a ticket, an owner, or an audit scope. Use it to
send a delete or a rename to the correct team.

Two values of the `field` column need an explanation. A row with the field
`dynamic` comes from a dynamic address group that matches the object by tag. The
`tags` column shows the tags of that group. It does not show the filter tags
that cause the match. Read the group filter to see those. A row with the field
`tag` comes from an object or a rule that carries the tag you trace. Thus that
tag is also in the tag list of the row.

If a name is ambiguous (exists in multiple kinds/locations), pass `--kind` and
`--location`:

```console
psc -c panorama.xml refs used web --kind address --location shared
```

## What's unused?

```console
psc -c panorama.xml refs unused --kind address
```

Unused is **recursive**: an object is "used" only if a rule reaches it directly
*or* through a chain of groups. A group that no rule references is unused, and so
are its members if nothing else reaches them.

`--kind` accepts `address`, `address-group`, `service`, `service-group`, `tag`.

### Objects used only by disabled rules

By default a disabled rule still counts as a reference (it can be re-enabled at
any time). Pass `--ignore-disabled` to treat disabled rules as *non*-references —
surfacing objects that are used **only** by disabled rules, a common cleanup
target once a rule set is retired:

```console
psc -c panorama.xml refs unused --kind address --ignore-disabled
```

### The blind-spot caveat

`refs unused` prints a one-line scan-scope caveat on **stderr** by default (stdout
stays pure machine output), restating that the list is *candidates*, not a
kill-list. Suppress it with `--no-caveat` once you've internalised the coverage
limits:

```console
psc -c panorama.xml refs unused --kind address --no-caveat -o json
```

!!! danger "`unused` means *unused by policy* — not *safe to delete*"
    psc only scans device-group objects and policy rulebases. Objects referenced
    from **templates / network / device config** (IKE gateways, GlobalProtect,
    service routes, log servers…) — or matched into a **dynamic address group**
    by an *externally registered* IP rather than a config tag — are reported
    `unused` even though they are in use. (Config-tag DAG membership *is* now
    resolved, so an address tagged into a rule-referenced DAG is kept.)
    Treat this list as **candidates**, verify `shared` objects in Panorama, and
    read **[Coverage and blind spots](coverage-and-limitations.md)** before
    deleting. Delete a verified candidate with [`psc delete`](#from-unused-to-deleted),
    which is reference-safe for the sites psc scans — but it cannot see the
    blind spots above, so a human must still check the list.

!!! tip "Cleanup order"
    Delete unused groups before unused objects, and always re-check
    `refs used` after each change — removing one reference can make another
    object newly unused.

### From `unused` to deleted

`refs unused` lists candidates; [`psc delete`](../reference/cli.md#delete)
removes them safely. The two compose over a pipe, because `delete -f -` reads
the machine output of `refs unused` directly — as JSON lines (`-o jsonl`) or as
one JSON array (`-o json`):

```console
psc -c panorama.xml -o jsonl refs unused --kind address --no-caveat \
  | psc -c panorama.xml delete -f -
```

That is a **dry-run**: it prints the plan and changes nothing. Read the plan and
the warnings first, then re-run with `--apply --out`.

An empty candidate list is a clean no-op. `delete -f` with zero targets prints
an empty plan and exits `0`, so a filter that matches nothing never breaks a
`set -e` script.

Put a `jq` filter between the two commands to narrow the list. Each `unused`
row carries a `tags` field, so this drops every tagged candidate — a tagged
address may join a dynamic address group at runtime:

```console
psc -c panorama.xml -o jsonl refs unused --kind address --no-caveat \
  | jq -c 'select(.tags | length == 0)' \
  | psc -c panorama.xml delete -f - --apply --out cleaned.xml
```

You can also review the list as a file first. `delete -f` accepts plain
`[kind:]name[@location]` lines with `#` comments, so a human can edit the file
before anything is deleted:

```console
psc -c panorama.xml -o csv refs unused --kind service > dead-services.csv
# review, then write the survivors as spec lines:
printf 'service:tcp-old@DG-EDGE\nservice-group:svcgrp-retired\n' > targets.txt
psc -c panorama.xml delete -f targets.txt
```

Targets also go on the command line, one per argument or per `--target`. The
kind defaults to `--kind` (`address`). A target that omits the location is
looked up in the config, and a name that exists in several locations is a
validation error (exit `4`) — qualify it as `kind:name@location`.

```console
psc -c panorama.xml delete h-unused service:tcp-old@DG-EDGE tag:t-retired
```

`delete` plans the same cascade as [`decommission`](editing-objects.md#decommission-an-address):
it scrubs every group member list and rule field whose name stops resolving
after the plan applies, deletes a rule left with an empty required field,
deletes a group the scrub empties, and removes the objects last. A name that
[falls through](safety.md#a-shadowed-name-falls-through) to a same-named object
above keeps its place, and the plan warns about it. It refuses the plan (exit
`6`) when it meets a reference it cannot rewrite. See
**[Writes and safety](safety.md#reference-safe-deletion-by-name)** for the full
blocker list.

!!! warning "`delete` is reference-safe — it is not a substitute for verification"
    `delete` protects the reference sites psc **scans**. It knows nothing about
    templates, network/device config, or runtime DAG membership. So the human
    check of the candidate list still applies, especially for `shared` objects.

## Dangling references

```console
psc -c panorama.xml refs dangling
```

Lists references that point at a name no object defines (and that isn't a
predefined name like `any`). These are latent config errors — a rule referencing
a deleted object, a typo in a group member.

## Overlapping and contained ranges

`refs` answers "who points at this name?"; `audit overlaps` answers a different
question — "do my address *values* step on each other?":

```console
psc -c panorama.xml audit overlaps
```

It reports each pair of address objects whose IP ranges **contain** or
**overlap** one another, once per pair. A `relationship` of `contains` means one
object is broader (the narrower one is redundant inside it); `overlaps` means two
ranges intersect without one fully enclosing the other. Only `ip-netmask` and
`ip-range` objects participate — FQDN and `ip-wildcard` have no comparable
numeric range.

```json
{
  "left_name": "h-web1", "left_location": "shared", "left_value": "10.0.0.10/32",
  "right_name": "h-web1-slash", "right_location": "shared", "right_value": "10.0.0.10",
  "relationship": "contains"
}
```

It's a **pure read** — no plan, no `--apply`. Scope it with the global
`-d/--device-group` (it only compares objects visible in that scope), and use
the global `--strict` to exit `5` when nothing overlaps (handy in CI):

```console
psc -c panorama.xml --strict audit overlaps || echo "address ranges overlap"
```

Overlaps are not automatically wrong — a host inside its subnet is normal — but
the report surfaces accidental duplicates and shadowed objects worth folding
together with [`dedup`](duplicates-and-merging.md).

## Services duplicating well-known ports

`audit services-vs-wellknown` flags **custom** service objects that just re-invent
a port PAN-OS already ships or that IANA reserves:

```console
psc -c panorama.xml audit services-vs-wellknown
```

Each row is a custom service whose *single* destination port matches either a
predefined PAN-OS service (e.g. `service-http`) or an IANA well-known port (e.g.
`ssh`). The `kind` column tells the two apart — a real predefined object versus a
bare well-known port number — so you can consolidate onto the predefined service
where one exists. Ranges and multi-port objects are never flagged.

```json
{
  "service_name": "my-ssh", "service_location": "shared",
  "protocol": "tcp", "port": "22",
  "canonical_name": "ssh", "kind": "well-known-port"
}
```

Like `overlaps` it's a **pure read** — scope with `-d/--device-group` and use the
global `--strict` to exit `5` when nothing matches.

## Scope and scripting

All three accept `-d/--device-group` to scope, `-o json` for machine output, and
`--strict` to turn a finding into a non-zero exit (handy in CI: fail the build
if `refs dangling` finds anything).

```console
psc -c panorama.xml --strict refs dangling || echo "config has dangling refs"
```

!!! note "Rulebase coverage"
    The reference graph covers address-groups, service-groups, and **every**
    object-referencing rulebase: security, NAT, PBF, decryption, authentication,
    QoS, application-override, DoS, SD-WAN, tunnel-inspect, and
    network-packet-broker. A PBF forwarding next-hop that names an address
    object is shown in where-used and blocks a merge/rename that would strand it
    (it has no flat member list to rewrite — edit it by hand, then re-run).
