# Concepts

A small mental model goes a long way.

## Snapshot

`psc` reads a config into an immutable **snapshot**: every address, address
group, service, service group, tag, security rule, and NAT rule it understands,
across `shared` and every device-group. Read commands query the snapshot; they
never mutate it.

## Locations and inheritance

An object lives in a **location**: `shared`, or a named device-group. A
reference inside a device-group resolves to a same-named object in that
device-group if one exists (a local **shadow**), otherwise to the `shared`
object. This is exactly why renames are dangerous, and `psc` models it
faithfully.

!!! note "v0.1 scope"
    Nested device-group hierarchies are flattened to the leaf; only
    "device-group shadows shared" inheritance is modelled.

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

The **reference graph** answers "who points at this object?" across groups,
security rules, and NAT (match *and* translation fields). It powers where-used,
unused detection, and the safe repointing that merge and rename rely on.

## Sources

- **Offline** (`--config file.xml`): read and rewrite an exported config.
- **Live** (`--profile name`): fetch the running config over the XML API.

Both produce the same snapshot, so every read command behaves identically.
