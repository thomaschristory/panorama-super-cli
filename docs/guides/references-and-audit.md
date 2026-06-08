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

!!! danger "`unused` means *unused by policy* — not *safe to delete*"
    psc only scans device-group objects and policy rulebases. Objects referenced
    from **templates / network / device config** (IKE gateways, GlobalProtect,
    service routes, log servers…) — or matched into a **dynamic address group**
    by an *externally registered* IP rather than a config tag — are reported
    `unused` even though they are in use. (Config-tag DAG membership *is* now
    resolved, so an address tagged into a rule-referenced DAG is kept.)
    Treat this list as **candidates**, verify `shared` objects in Panorama, and
    read **[Coverage and blind spots](coverage-and-limitations.md)** before
    deleting. (Unlike delete, `merge`/`rename` are protected — they block when a
    reference can't be repointed.)

!!! tip "Cleanup order"
    Delete unused groups before unused objects, and always re-check
    `refs used` after each change — removing one reference can make another
    object newly unused.

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
