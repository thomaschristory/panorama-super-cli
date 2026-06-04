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

## The safety gate

`psc` **refuses** (exit `6`) a merge that would change meaning or that it can't
perform safely:

- **Different values.** Merging objects with different values changes what rules
  match. Blocked unless you pass `--allow-value-change`.
- **Invisible survivor.** If the kept object isn't visible where a reference
  lives (e.g. it's in another device-group), the merge is blocked rather than
  creating a dangling reference.

```console
$ psc -c panorama.xml -o json dedup merge --keep net-10 --remove local-only --remove-location DG-EDGE
{"error": "plan blocked (unsafe): value mismatch: ...", "type": "conflict", ...}
$ echo $?
6
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
