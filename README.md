# panorama-super-cli (`psc`)

[![PyPI](https://img.shields.io/pypi/v/panorama-super-cli.svg)](https://pypi.org/project/panorama-super-cli/)
[![CI](https://github.com/thomaschristory/panorama-super-cli/actions/workflows/test.yml/badge.svg)](https://github.com/thomaschristory/panorama-super-cli/actions/workflows/test.yml)
[![Docs](https://github.com/thomaschristory/panorama-super-cli/actions/workflows/docs.yml/badge.svg)](https://thomaschristory.github.io/panorama-super-cli/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**Agent-friendly object management for Palo Alto Panorama.** Find where an IP
lives, hunt down duplicate address/service objects, merge them safely (rewriting
every group and rule that referenced them), enforce naming conventions, and
audit object hygiene — all **dry-run by default**, with **PAN-OS `set`** and
**JSON** output for humans and agents alike.

```console
$ psc --config panorama.xml find ip 10.0.0.10
$ psc --config panorama.xml dedup addresses
$ psc --config panorama.xml dedup merge --keep h-web1 --remove web-primary --apply --out fixed.xml
```

> **v1.0.0.** From this release `psc` follows [SemVer](https://semver.org/): the
> CLI surface, JSON contracts, and exit codes are stable public API. Writes are
> dry-run by default; nothing touches Panorama without `--apply`.

## Why

Panorama configs rot: the same `10.0.0.10` ends up as `h-web1`, `web-primary`,
and `WEB_PRD_01`; services duplicate well-known ports; objects outlive the rules
that used them. `psc` gives you a fast, scriptable, **safe** way to see and fix
that — offline against an exported config, or live against Panorama.

## Install

```console
uv tool install panorama-super-cli      # recommended
# or
pipx install panorama-super-cli
# or
pip install panorama-super-cli
```

## Two ways to point it at a config

- **Offline** (no credentials, totally safe): `psc --config exported.xml <cmd>`.
  Export from Panorama (`scp export configuration ...` or the GUI) and audit it
  on your laptop.
- **Live**: configure a profile (`psc init`, `psc login`) and `psc` talks the
  PAN-OS XML API via [`pan-os-python`](https://github.com/PaloAltoNetworks/pan-os-python).
  Reads are free; writes still require `--apply`.

## What it does

| Area | Commands |
| --- | --- |
| **Find / resolve** | `psc find ip <ip>`, `find ip -e <ip>` (exact only), `find ip --resolve-fqdn` (opt-in DNS), `find ip -f ips.txt`, `find object <name>` |
| **Duplicates** | `psc dedup addresses`, `dedup services`, `dedup groups`, `dedup merge` (pairwise or `--group <value>`), `dedup merge-group` |
| **Audit** | `psc audit overlaps` (overlapping/contained CIDR ranges), `audit services-vs-wellknown` |
| **Diff** | `psc diff a.xml b.xml`, `diff --device-group A --against B` |
| **Object CRUD** | `psc set address\|address-group\|service\|service-group\|tag ...` (create/update with PAN-OS validation) |
| **Import / export** | `psc export <kind>` (NDJSON), `psc set <kind> -f objs.ndjson` (bulk import) |
| **Rule edits** | `psc rule edit-member --rule R --field F --add/--remove M` (idempotent) |
| **Decommission / move** | `psc decommission <ip\|cidr>...` (reference-safe teardown), `psc move <kind> <name> --from --to [--cascade]` |
| **Naming** | `psc name lint`, `name apply --object` / `--all` (opt-in templates) |
| **References** | `psc refs used <object>`, `refs unused [--ignore-disabled]`, `refs dangling` |
| **Workbench** | `psc workbench` (`psc w`) — interactive TUI, full CLI parity |
| **Output** | `--output json|set|table|yaml|csv|jsonl` |

See the [docs](https://thomaschristory.github.io/panorama-super-cli/) for the
full surface, the safety model, and the agent guide.

## Workbench (interactive TUI)

Prefer a cockpit to one-shot commands? `psc workbench` (alias `psc w`) is a
keyboard-driven [Textual](https://textual.textualize.io/) TUI at full CLI parity.
Search objects, multi-select them into a persistent buffer, route the selection
into a spoke (dedup, move, rename, decommission, rule edits, audits, naming,
create, …), and stage plans into a git-like changelist that applies as one batch
— as a `set` script, an offline config write, or a live candidate push (never a
commit). Same safety model as the CLI throughout.

```console
psc --config panorama.xml workbench
psc -p prod w --output-mode live-apply
```

See the [Workbench guide](https://thomaschristory.github.io/panorama-super-cli/guides/workbench/).

## Safety model

- **Dry-run is the default.** Every mutating command prints a plan and exits
  without touching anything unless you add `--apply`.
- **Side-effect aware.** Merging or renaming an object rewrites every address
  group, security rule, and NAT rule that referenced it — across `shared` and
  device-groups — or refuses and tells you why.
- **`--debug`** streams structured logs to stderr; stdout stays clean for pipes.

## For AI agents

`psc` ships a bundled [Agent Skill](skills/panorama-super-cli/SKILL.md) and
emits a stable JSON envelope + exit-code contract. Pass `--output json` and
parse away. See [Using with AI agents](https://thomaschristory.github.io/panorama-super-cli/guides/using-with-ai-agents/).

## License

[Apache-2.0](LICENSE).
