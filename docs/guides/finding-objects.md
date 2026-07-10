# Finding objects

## `find ip` — resolve an address

```console
psc -c panorama.xml find ip 10.0.0.10
```

Returns every address object that relates to the target, classified by **match
kind**:

| Match | Meaning |
| --- | --- |
| `exact` | The object's value equals the target. |
| `contains` | The object is broader and contains the target (a `/24` for a host). |
| `within` | The object is narrower and sits inside the target (a host inside a queried `/24`). |

It also lists the **address-groups** that carry any matched object, so you can
see at a glance which groups (and therefore rules) already cover this IP.

`exists` is `true` when there's at least one `exact` match — the quick "is this
already an object?" signal.

### Targets

`find ip` accepts a host, a CIDR, a range, or an FQDN:

```console
psc -c panorama.xml find ip 10.0.0.0/24      # every object inside the /24
psc -c panorama.xml find ip 10.0.0.50-10.0.0.60
psc -c panorama.xml find ip example.com      # FQDN objects (exact name match)
```

### Resolving FQDN objects (opt-in DNS)

By default `find ip` never touches the network — an FQDN object only matches when
you query its literal name. Pass `--resolve-fqdn` to **DNS-resolve** FQDN objects
and match those whose A/AAAA records include the queried IP:

```console
psc -c panorama.xml find ip 93.184.216.34 --resolve-fqdn
```

Resolution is cached and timeout-bounded; objects whose lookup fails are skipped
with a count on stderr (stdout stays clean). This is strictly opt-in — the
offline default stays hermetic and reproducible, so leave it off in CI.

### A whole list

```console
psc -c panorama.xml find ip -f ips.txt -o json | jq '.[] | select(.exists | not)'
```

`-f` reads one target per line (`#` comments allowed). The JSON output is an
array of per-target results — perfect for finding which IPs are *not* yet
objects.

## `find object` — locate by name

```console
psc -c panorama.xml find object grp-web
```

Finds every object with that exact name, across all kinds and locations. Useful
when the same name exists in `shared` and a device-group.

## Open an object

A one-line summary (`static[7]`) tells you a group exists but not what's *in* it.
Add `-x/--expand`, or use the `show` alias, to open it:

```console
psc -c panorama.xml show grp-web
psc -c panorama.xml find object grp-web -x     # identical
```

For a plain address or service you get its value. For an **address-group** or
**service-group** you get the nested member **tree** and the **effective leaves**
— the deduped, flattened set of addresses/ports the group actually resolves to,
expanding nested groups recursively:

```console
address-group grp-web @shared
├── address h-web1 @shared = 10.0.0.10/32
└── address-group grp-web-extra @shared
    └── address h-web2 @shared = 10.0.0.11/32
effective: 2 leaf value(s)
  • 10.0.0.10/32
  • 10.0.0.11/32
```

A **tag** lists every object carrying it; a **rule** groups its resolved
`source`/`destination`/`service` members by field. Unresolvable members are shown
and flagged, never dropped — `dynamic` (filter-based group), `dangling`
(unresolved reference), `cycle` (nested-group loop) — and in `-o json` an
`effective_complete: false` (plus a stderr warning) marks a partial leaf set.
It's a pure read: nothing is staged or written.

In the [workbench](workbench.md), press `v` on a search result for the same view.

## Scope

Restrict to one device-group (plus inherited `shared`) with `-d/--device-group`:

```console
psc -c panorama.xml -d DG-EDGE find ip 192.168.1.1
```

## Strict mode

`--strict` makes a no-match an error (exit `5`), for scripting:

```console
psc -c panorama.xml --strict find ip 203.0.113.9 || echo "not an object"
```
