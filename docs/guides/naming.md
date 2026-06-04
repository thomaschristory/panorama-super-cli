# Naming templates

Naming is **opt-in**. `psc` never renames anything unless you ask — but when you
do, it computes the name your template implies and renames *safely*.

## The scheme

A `NamingScheme` maps each value-kind to a format string. The defaults:

| Kind | Template | Example |
| --- | --- | --- |
| Host (`/32`) | `H-{ip}` | `H-10.0.0.10` |
| Network | `N-{network}_{prefix}` | `N-10.0.0.0_24` |
| Range | `R-{start}-{end}` | `R-10.0.0.50-10.0.0.60` |
| FQDN | `FQDN-{fqdn}` | `FQDN-example.com` |
| TCP service | `tcp-{port}` | `tcp-443` |
| UDP service | `udp-{port}` | `udp-53` |

Override any subset in `~/.psc/config.yaml` under `defaults.naming` (set
`lowercase: true` to force lower-case). Generated names are sanitized to PAN-OS
rules (≤63 chars, leading alphanumeric, allowed character set).

## Lint for drift

```console
psc -c panorama.xml name lint
```

Reports every object whose name differs from what the scheme implies. Add
`--all` to include already-compliant objects; `--strict` to exit non-zero on
drift (CI gate).

## Rename one object

A reference-aware rename — repoints every group/rule reference, just like a
merge:

```console
psc -c panorama.xml name rename --object h-web1 --to H-10.0.0.10
psc -c panorama.xml name apply  --object h-web1   # rename to the scheme name
```

Both are dry-run by default. Offline, `--out fixed.xml` writes the rewritten
config (add `--apply` to execute; the file is written either way). Add
`-of set` for a PAN-OS `set` script instead. Live, `--apply` pushes the
candidate; add `--out` to also save the artifact.

## The shadow guard

Renaming a `shared` object to a name a device-group already defines (or vice
versa) would silently re-point that device-group's references. `psc` **refuses**
such a rename:

```console
$ psc -c panorama.xml name rename --object src --to clash
{"error": "plan blocked (unsafe): device-group 'DG1' already defines 'clash' ...",
 "type": "conflict"}
```

This is the single most common way a "harmless" rename breaks traffic — so it's
a hard blocker, not a warning.
