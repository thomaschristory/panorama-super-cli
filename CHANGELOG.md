# Changelog

All notable changes to `panorama-super-cli` are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/), and from v1.0.0 the
project will follow [Semantic Versioning](https://semver.org/). While on
`0.x`, minor versions may include breaking changes.

## [Unreleased]

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
