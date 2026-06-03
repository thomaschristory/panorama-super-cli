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
