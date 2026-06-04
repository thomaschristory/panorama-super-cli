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
