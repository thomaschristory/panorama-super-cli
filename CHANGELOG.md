# Changelog

All notable changes to `panorama-super-cli` are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/), and from v1.0.0 the
project will follow [Semantic Versioning](https://semver.org/). While on
`0.x`, minor versions may include breaking changes.

## [Unreleased]

## v0.4.0 — 2026-06-05

### Added

- **`psc audit overlaps`** (#8) — report address objects whose CIDR/range
  intervals contain or overlap one another (e.g. a `/32` that sits inside a
  `/24` that is also an object), to surface redundant or shadowing objects.
  Pure read; table/json; honours device-group scope; `--strict` exits `5` when
  none. A sort-then-sweep keeps it off the O(n²) path; only EXACT/WITHIN-style
  interval relationships are emitted, per IP family.
- **`psc dedup groups`** + **`psc dedup merge-group`** (#10) — group-level
  deduplication. `dedup groups` buckets address-groups that resolve to the same
  *effective* set of addresses under different names (recursively, cycle-safe),
  warning about any dynamic/unresolvable groups it had to skip. `merge-group`
  collapses one group into another by reusing the repoint-before-delete merge
  engine; it blocks on non-equivalent effective sets, on nested/cyclic pairs
  (which would create a self-referential group), and when the kept group isn't
  visible at a referrer's scope.
- **`psc set address|address-group|service|service-group|tag`** (#6) —
  create or update a single object with client-side PAN-OS validation
  (name ≤63 leading-alnum, description ≤255, tag name ≤127, exactly one address
  value kind, `--dest-port` required for services, `colorN` tag colours).
  Validation errors exit `4`; a cross-kind name collision or an in-place
  type/mode change on update is a blocker (exit `6`). Dry-run `set` plan;
  offline `--apply` (live updates of existing objects are refused — use offline).
- **`psc rule edit-member --add/--remove`** (#7) — idempotently add or remove
  one member of a rule field (`source`/`destination`/`service`/`application`).
  Because PAN-OS `set` on a member field *appends*, a removal renders
  `delete <field>` + `set <field> [ … ]`; re-running any edit is a no-op. NAT's
  scalar `service` is blocked; `application` is valid only on security rules.
- **`psc decommission <ip|cidr|file>`** (#5) — flagship reference-safe teardown.
  Given the address objects matching an IP/CIDR/list, it scrubs them from every
  group and rulebase, deletes rules left orphaned (no real source *or* no real
  destination — `any` survives), deletes emptied groups, and deletes the objects
  — in that order, dry-run by default. The teardown **cascades to a fixpoint**:
  deleting an emptied group repoints/orphans the rules and parent groups that
  named it, so a referent is never removed before its references are rewritten.
  References in nested NAT-translation / PBF-nexthop fields, and dynamic-group
  filter-tag matches, are hard blockers; orphan-rule deletions are warnings.
  `--keep-groups` / `--keep-rules` narrow the teardown. Adds the `RuleDelete`
  changeset op (offline and live appliers updated atomically).

### Documentation

- New "Editing objects" guide (set / rule edit-member / decommission) and
  expanded audit, duplicates, and safety guides; CLI reference, README, and the
  contributor architecture cheat-sheet updated for all five commands.

## v0.3.1 — 2026-06-04

### Added

- **Coverage & blind-spots documentation** plus a point-of-use caveat (#56). A
  new "Coverage and blind spots" guide maps exactly what the reference graph
  scans and — critically — what it does not (templates and network/device
  config, dynamic-address-group membership, profiles/schedules/EDLs/regions/
  applications, and the single-snapshot scope). `refs unused` now prints a
  one-line caveat to **stderr** restating these gaps so the list isn't mistaken
  for a kill-list; stdout stays pure machine output. The core message: `unused`
  means *unreferenced by policy*, **not** *safe to delete* — `merge`/`rename`
  are protected (they block when they can't repoint), deletion is not.

### Fixed

- **NAT rule tags are now scanned** (#55). The reference graph walked NAT rules'
  address and service fields but skipped their tags — the only rulebase whose
  tags were missed. A tag used only on a NAT rule was reported `unused` (and so
  deletable) and was not repointed on rename. NAT rule tags now behave like
  every other rulebase's.

## v0.3.0 — 2026-06-04

### Added

- **Where-used across every object-referencing rulebase** (#2). The reference
  graph previously covered only `security` and `nat`; it now also scans PBF,
  decryption, authentication, QoS, application-override, DoS, SD-WAN,
  tunnel-inspect, and network-packet-broker. `refs used`/`unused`/`dangling`
  and the merge/rename repointing all account for them, so an object referenced
  only by, say, a QoS or PBF rule is no longer reported unused (and can no
  longer be deleted out from under a live rule). A PBF forwarding next-hop that
  names an address object is shown in where-used and **blocks** a merge/rename
  that would strand it (it has no flat member list to rewrite — edit it by hand,
  then re-run). The `referrer_kind` in the output (`qos-rule`, `pbf-rule`, …)
  names the exact rulebase.

### Fixed

- **Tag rename/merge no longer wipes a rule's other tags** (#2). `field_members`
  read the wrong attribute name for a rule's `tag` field, so renaming or merging
  a tag referenced by a security rule rewrote the rule's tag list to an empty (or
  single-entry) value, destroying its co-tags. All rule kinds now resolve the
  field uniformly.

## v0.2.6 — 2026-06-04

### Added

- **`psc version` and `psc version check`** (#33). `psc version` prints the
  installed version (the format-aware equivalent of the `--version` flag, which
  is kept); `psc version check` queries PyPI and reports whether a newer release
  is available, exiting 0 either way. An unreachable PyPI surfaces as a typed
  `transport` error rather than a stack trace.

### Fixed

- **`psc profile list` now prints the config file's location** (#48). The path
  is platform-dependent (notably different on Windows), so it was hard to find
  where psc reads/writes profiles. The path is written to stderr — keeping the
  machine-readable rows on stdout clean — and flagged `(not created yet)` when
  the file is absent.
- **`--out` now writes the artifact even in a dry-run, and on a live profile**
  (#47). Previously `dedup merge`/`name` silently ignored `--out` unless
  `--apply` was passed, and ignored it entirely on a live profile — so
  `dedup merge … -of set --out out.txt` against a live Panorama printed the plan
  but wrote no file. `--out` is now treated as an artifact request (a `set`
  script or rewritten `xml`): writing a user-named file never touches the source
  export or the live candidate, so it is honoured regardless of `--apply` or
  source. `--apply` still governs the live candidate push; combining
  `--apply --out` on a live profile both pushes and saves the artifact. Offline
  `--apply` still requires `--out`. The blocker gate holds on every path.

## v0.2.5 — 2026-06-04

### Fixed

- **`psc find ip` table output now separates each target's matches with a
  horizontal rule** (#43). Resolving several targets at once (notably with
  `--file`) previously printed every match as one undifferentiated block, so it
  was hard to see where one target's matches ended and the next began. The
  table now draws a rule between per-target blocks. Machine output formats
  (json/jsonl/yaml/csv/set) are unchanged.

## v0.2.4 — 2026-06-04

### Changed

- **`psc dedup addresses` is now strict by default** (#38). Addresses are
  grouped only when their values are byte-identical, so a host accidentally
  written with a subnet mask (`10.1.1.50/24`) is no longer reported as a
  duplicate of the network `10.1.1.0/24` — merging those would have silently
  changed rule matching. Genuinely identical forms (`10.0.0.10` and
  `10.0.0.10/32`) still group. Pass `--not-strict` for the previous
  host-bit-masking behaviour. The `dedup merge` safety gate now compares exact
  values too, so such a merge is blocked unless `--allow-value-change` is given.

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
