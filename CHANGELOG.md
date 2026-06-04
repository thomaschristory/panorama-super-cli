# Changelog

All notable changes to `panorama-super-cli` are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/), and from v1.0.0 the
project will follow [Semantic Versioning](https://semver.org/). While on
`0.x`, minor versions may include breaking changes.

## [Unreleased]

## v0.2.3 — 2026-06-04

### Added

- **`-of` / `--output-format xml|set` for the offline `--apply --out` artifact.**
  Mutating commands (`dedup merge`, `name rename`, `name apply`) can now write the
  rewritten config file as the equivalent PAN-OS `set` script instead of XML.
  `xml` (the rewritten, `load config`-able config) stays the default, so existing
  invocations are unchanged; `set` writes the creates/deletes/repoints that
  achieve the same change, which is easier to read and to paste into a config
  session or `load config partial`. This is distinct from `-o set`, which renders
  the dry-run plan to stdout. The blocker hard-gate and repoint-before-delete
  ordering apply to both formats; a blocked plan writes no file. (#37)

## v0.2.2 — 2026-06-04

### Fixed

- **Repoint-before-delete no longer skipped silently.** A merge or rename whose
  only safe repoint is a reference the appliers can't express as a flat member
  rewrite — a NAT source/destination *translation* field, or a rule edit with no
  rulebase — is now refused with a `blocker` (exit 6) on both the offline and
  live paths, naming the object and the unmappable referrer. Previously the edit
  was dropped while the delete/rename still ran, leaving a dangling reference.
  Advisory unmappable edits with no accompanying teardown still pass with a
  warning. (#28)
- **`psc` with no arguments (and unknown commands) print help, not a traceback.**
  Typer 0.16+ vendors its own Click, so the exceptions the entry point raised
  were not the `click.*` the wrapper caught and escaped as a traceback ending in
  `NoArgsIsHelpError`. The wrapper now handles both the vendored and the real
  Click hierarchies, resolving the module defensively so psc still imports on
  older Typer. No-args/`--help` exit 0; usage errors exit 2. (#31)

## v0.2.1 — 2026-06-04

### Added

- **`find ip --exact` / `-e`.** Restrict `find ip` to objects whose value
  equals the target exactly, dropping the broader (`contains`) and narrower
  (`within`) matches that a host query normally also surfaces. Netmask and
  bare-host forms still canonicalize equal, so `10.0.0.10` and `10.0.0.10/32`
  remain exact matches of each other. Address-groups are reported only when
  they carry an exact match. (#30)

## v0.2.0 — 2026-06-04

### Added

- **Live `--apply`.** Mutating commands can now push their plan to Panorama's
  candidate config over the XML API against a profile (`-p`), not just offline
  to a file. `psc` **never commits** — it leaves a reviewable candidate, the
  device-side analog of the offline `--out` file. The `blockers` gate and
  repoint-before-delete ordering are enforced on the wire before any device
  contact; a name that can't be addressed by an xpath (single quote) is
  rejected up front; a mid-plan failure reports how far it got and leaves the
  uncommitted candidate for inspection. Live *updates* of an existing object
  remain offline-only for now (apply with `--out`).

## v0.1.0 — 2026-06-03

First public release. Agent-friendly Panorama object management, offline or live.

### Added

- **Offline + live sources.** Read an exported config with `--config file.xml`,
  or a live Panorama via a profile (`psc profile add`). The same XML parser
  feeds both paths.
- **`psc find ip`** — resolve an IP / CIDR / range / FQDN (or a `--file` list)
  to the address objects that match it: exact, containing (broader), and
  within (narrower), plus the address-groups that carry them.
- **`psc find object`** — locate any object by exact name across all kinds and
  locations.
- **`psc dedup addresses|services`** — find objects that share an identical
  value under different names (e.g. `10.0.0.10` as `h-web1` *and* `web-primary`).
- **`psc dedup merge`** — collapse one object into another, repointing every
  group/security-rule/NAT reference *before* deleting it. Refuses (blocks)
  value-mismatch merges and references that can't be safely repointed.
- **`psc refs used|unused|dangling`** — where-used pre-flight, recursive unused
  detection (objects no rule reaches even through groups), and dangling-reference
  audit.
- **`psc name lint|rename|apply`** — opt-in naming templates; report drift and
  perform reference-aware renames that refuse the shared-vs-device-group shadow
  collision.
- **Safety model.** Dry-run by default; `--apply` is the only path to a write.
  Offline `--apply --out fixed.xml` produces a loadable, cleaned config without
  touching the source export. `ChangeSet.blockers` is a hard refusal gate.
- **Output formats** `table, json, jsonl, yaml, csv, set` with a stable JSON
  error envelope and typed exit codes; non-TTY stdout auto-switches to JSON.
  `-o set` emits ready-to-paste PAN-OS `set` commands (member edits render as
  delete-then-set, so they're idempotent rather than additive).
- **Agent Skill** bundled at `skills/panorama-super-cli/SKILL.md`.

### Known limitations (tracked for v0.2)

- Live `--apply` is not yet implemented; use `-o set` or offline `--apply`.
- Where-used covers address-groups, security rules, and NAT (match +
  translation). PBF, decryption, authentication, QoS, and other rulebases are
  not yet scanned.
- Nested device-group hierarchies are flattened to the leaf; only
  device-group-shadows-shared inheritance is modelled.
