# panorama-super-cli

**Agent-friendly object management for Palo Alto Panorama.** Find where an IP
lives, hunt down duplicate address/service objects, merge them safely (rewriting
every group and rule that referenced them), enforce naming conventions, and
audit object hygiene — **dry-run by default**, with **PAN-OS `set`** and **JSON**
output for humans and agents alike.

```console
$ psc --config panorama.xml find ip 10.0.0.10
$ psc --config panorama.xml dedup addresses
$ psc --config panorama.xml dedup merge --keep h-web1 --remove web-primary --apply --out fixed.xml
```

!!! note "Stability"
    `psc` is past `1.0` and follows [semantic versioning](https://semver.org/):
    exit codes and JSON contracts are stable within a major version. Writes are
    dry-run by default; nothing changes a config without `--apply`.

## Why

Panorama configs rot. The same `10.0.0.10` becomes `h-web1`, `web-primary`, and
`WEB_PRD_01`; services duplicate well-known ports; objects outlive the rules
that used them. `psc` gives you a fast, scriptable, **safe** way to see and fix
that — offline against an exported config, or live against Panorama.

## Two ways in

- **Offline** — `psc --config exported.xml <cmd>`. No credentials, totally
  read-only against your device. Apply changes to a *new* file.
- **Live** — configure a profile, then `psc --profile prod <cmd>`. Reads go
  over the PAN-OS XML API; writes still require `--apply`.

See [Concepts](getting-started/concepts.md) for the mental model, or jump to
[Finding objects](guides/finding-objects.md).

## At a glance

| Area | Commands |
| --- | --- |
| Find / resolve | `find ip`, `find object`, `show` (open an object) |
| Duplicates | `dedup addresses`, `dedup services`, `dedup merge` |
| References | `refs used`, `refs unused`, `refs dangling` |
| Edit | `set`, `rule edit-member`, `group edit-member`, `move`, `decommission` |
| Naming | `name lint`, `name rename`, `name apply` |
| Interactive | `workbench` (`w`) — TUI over every engine |
| Profiles | `profile list/add/remove` |

Everything is built on a [safety model](guides/safety.md) you can trust.
