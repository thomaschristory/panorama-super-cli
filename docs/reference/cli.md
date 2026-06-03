# CLI reference

Run `psc --help`, or `psc <group> --help`, for the authoritative, version-exact
surface. This page is an overview.

## Global options

These are **context** options — pass them *before* the subcommand:

| Option | Description |
| --- | --- |
| `-c, --config PATH` | Offline: exported Panorama config XML. |
| `-p, --profile NAME` | Live: profile from `~/.psc/config.yaml`. |
| `-o, --output FMT` | `table`, `json`, `jsonl`, `yaml`, `csv`, `set`. |
| `-d, --device-group NAME` | Scope to one device-group (plus shared). |
| `--strict` | Exit non-zero when a lookup finds nothing / finds problems. |
| `--debug` | Verbose structured logs on stderr. |
| `--version` | Print version and exit. |

Write-execution options (`--apply`, `--out`) belong to the individual mutating
commands and are passed *after* the command.

## Commands

### find

```
psc find ip <target>... [-f FILE]
psc find object <name>
```

Resolve an IP/CIDR/range/FQDN (or a file of them) to objects; or locate an
object by exact name. See [Finding objects](../guides/finding-objects.md).

### dedup

```
psc dedup addresses
psc dedup services
psc dedup merge --keep NAME --remove NAME [--location LOC]
               [--keep-location LOC] [--remove-location LOC]
               [--allow-value-change] [--apply] [--out PATH]
```

Find duplicates; merge one object into another, repointing all references. See
[Duplicates and merging](../guides/duplicates-and-merging.md).

### refs

```
psc refs used <name> [--kind KIND] [--location LOC]
psc refs unused [--kind KIND]
psc refs dangling
```

Where-used, recursive unused, and dangling-reference audit. See
[References and audit](../guides/references-and-audit.md).

### name

```
psc name lint [--all]
psc name rename --object OLD --to NEW [--kind KIND] [--location LOC] [--apply] [--out PATH]
psc name apply  --object NAME            [--location LOC] [--apply] [--out PATH]
```

Opt-in naming-template lint and reference-aware rename. See
[Naming templates](../guides/naming.md).

### profile

```
psc profile list
psc profile add --name N --host H [--api-key K] [--port P] [--device-group DG] [--default]
psc profile remove <name>
```

Manage live connection profiles. See [Configuration](config.md).
