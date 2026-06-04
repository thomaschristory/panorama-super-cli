# Concepts

A small mental model goes a long way.

## Snapshot

`psc` reads a config into an immutable **snapshot**: every address, address
group, service, service group, tag, and rule it understands — security, NAT,
and the nine other rulebases that reference objects (PBF, decryption,
authentication, QoS, application-override, DoS, SD-WAN, tunnel-inspect,
network-packet-broker) — across `shared` and every device-group. Read commands
query the snapshot; they never mutate it.

## Locations and inheritance

An object lives in a **location**: `shared`, or a named device-group. A
reference inside a device-group resolves to its **closest** definition up the
device-group chain — the device-group itself, then each parent, then `shared`
(a nearer definition is a local **shadow** of the inherited one). This is
exactly why renames are dangerous, and `psc` models it faithfully.

Nested device-group hierarchies are read from the config's read-only
`parent-dg` metadata, so where-used, unused, and dangling analysis — and the
merge/rename shadow guards — all account for multi-level inheritance across
ancestors and descendants. A config with no hierarchy metadata is treated as a
flat single level (every device-group a direct child of `shared`).

## Value vs name

`psc` compares objects by **meaning**, not name. `10.0.0.10`, `10.0.0.10/32`,
and (after normalization) any equivalent form collapse to the same value — which
is how it finds duplicates and resolves an IP to objects regardless of naming.

## Change-set

Every write produces a **change-set**: an ordered, inspectable plan
(reference rewrites → renames → deletes). Dry-run prints it; `--apply` executes
it. A change-set with **blockers** is unsafe and is refused — even with
`--apply`. See [Writes and safety](../guides/safety.md).

## Reference graph

The **reference graph** answers "who points at this object?" across groups and
every object-referencing rulebase — security, NAT (match *and* translation
fields), and PBF, decryption, authentication, QoS, application-override, DoS,
SD-WAN, tunnel-inspect, and network-packet-broker. It powers where-used, unused
detection, and the safe repointing that merge and rename rely on.

## Sources

- **Offline** (`--config file.xml`): read and rewrite an exported config.
- **Live** (`--profile name`): fetch the running config over the XML API.

Both produce the same snapshot, so every read command behaves identically.
