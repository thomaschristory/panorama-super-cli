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

### init

```
psc init [--name N] [--host H] [--port P] [--device-group DG] \
         [--user U | --api-key K] [--no-verify] [--default/--no-default]
```

Interactively bootstrap the first live profile. With `--user` (or an
interactive prompt) it exchanges a username/password for an API key via the
PAN-OS keygen API, runs a pre-flight probe, and writes a `0600` config. Pass
`--api-key` to store a key you already have instead of generating one. The
password is read from `$PSC_PASSWORD` or a hidden prompt — never a flag.

### login

```
psc login [--name N] [--user U]
```

Verify a stored profile's API key with a `show system info` probe (selects the
profile from `--name`, then `--profile`, then the default). With `--user` it
re-generates (rotates) the key first and only persists it once the probe
succeeds. Auth failures exit `8`, unreachable/transport failures exit `7`.

### profile

```
psc profile list
psc profile add --name N --host H [--api-key K] [--port P] [--device-group DG] [--default]
psc profile remove <name>
```

Manage live connection profiles. `init`/`login` are the friendlier front door;
`profile add` is the scriptable, non-interactive form. See
[Configuration](config.md).
