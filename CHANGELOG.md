# Changelog

All notable changes to `panorama-super-cli` are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/), and from v1.0.0 the
project will follow [Semantic Versioning](https://semver.org/). While on
`0.x`, minor versions may include breaking changes.

## [Unreleased]

### Changed

- **Workbench: the hub layout is restacked**
  ([#149](https://github.com/thomaschristory/panorama-super-cli/issues/149)) —
  the search box and the `staged (N)` strip now share the top row, and the
  results and selection tables stack vertically below at the full terminal
  width instead of splitting it side by side. The results table — the widest
  content in the app — no longer gets squeezed to half the screen and
  truncating its columns.
- **Workbench: the footer no longer lists all the bindings**
  ([#150](https://github.com/thomaschristory/panorama-super-cli/issues/150)) —
  it now shows just `? keys`, `ctrl+p commands`, and `q quit`. `?` opens a new
  **keymap overlay** listing every command grouped by what it does, with a
  real description; `ctrl+p` opens the **command palette**, now rendering
  `psc`'s own commands as `Category › Title` with the description as help text
  and ranked above Textual's built-in commands (theme, screenshot) — so
  `Dedup` and `Duplicate scan` are finally distinguishable. **No key was
  reassigned**: every existing hotkey still works from the hub, it's just no
  longer advertised across the bottom of the screen.

## v1.7.0 — 2026-07-13

### Added

- **Workbench: build a group from the selection (`N`)**
  ([#146](https://github.com/thomaschristory/panorama-super-cli/issues/146)) —
  the find session's payoff. Search, `space` the objects you want, press `N`,
  name the group: the selection becomes a new static group. `G` adds the
  selection to a group that already exists; `N` makes one out of it. The kind
  follows the selection (addresses and address-groups make an address-group,
  services a service-group; a selection mixing the two namespaces is refused),
  and the location picker defaults to the narrowest location whose visibility
  cone covers every member. Staging clears the selection.
- Because the workbench knows *which objects* you pointed at — not just their
  names — `N` refuses the two ways a member reference silently means something
  else. A member the group's location cannot see (a `DG-NYC` object in a `shared`
  group) would dangle; a member whose name is **shadowed** from that location (you
  picked `web`@shared, but the group lives in a device-group that defines its own
  `web`) would bind to the wrong object, and PAN-OS has no syntax to say
  otherwise. Both are blockers. A group already at that name and location blocks
  too — `N` creates, `G` grows. `psc set address-group` is unchanged: it takes bare
  names and cannot make that check.

### Fixed

- The workbench's group screens no longer swallow the member list they preview:
  Textual parses `[ a, b ]` as markup and dropped it (the same class of bug as
  [#129](https://github.com/thomaschristory/panorama-super-cli/issues/129)).

## v1.6.0 — 2026-07-13

### Fixed

- **`dedup merge` no longer refuses to collapse a device-group's local shadow**
  ([#144](https://github.com/thomaschristory/panorama-super-cli/issues/144)) —
  merging `'web'@DG-EDGE` into an identical `'web'@shared` was blocked with
  *"kept object 'web' is not visible there"*. It was: the visibility gate
  resolved the kept name against the config as it stands *today*, in which the
  object being dropped is itself the shadow standing between the referrer and
  the survivor. The gate now resolves against the config **as the plan leaves
  it**, so the upward walk reaches the survivor. Blockers that are real still
  fire — an *intermediate* device-group carrying the same name (in `shared` →
  `DG-EMEA` → `DG-EDGE`) still stops the walk short of the survivor and is
  refused.
- A bucket merge (`--group`) now hides **every** object it deletes from name
  resolution, not just the one pair being planned, so a sibling duplicate on its
  way out is no longer mistaken for a blocking shadow.
- Addresses and address-groups share one PAN-OS namespace, so a merge now refuses
  when the kept *or* the dropped name is occupied by an object of the **other
  kind**: deleting the address `web`@DG-A leaves an address-group of that name
  standing, and it goes on shadowing everything above it. Repointing a rule onto
  that name would have aimed it at a different object.
- A **blocked** plan no longer carries warnings describing what it "will" do. It
  cannot run; `blockers` is the whole message.

### Changed

- **`dedup merge --group` picks a different default survivor.** Omitting
  `--keep` now keeps the member **highest in the device-group hierarchy**
  (`shared`, else the device-group nearest the root), where it previously sorted
  location names alphabetically and so kept a device-group copy over the `shared`
  one. Collapsing upward is what makes a duplicate disappear for every
  device-group at once. A member sitting in an unrelated device-group branch,
  which the other members' rules could never resolve, is skipped however high it
  sits — keeping it would only block the merge. Pass `--keep` to override.
- A same-name collapse now plans **only the delete** — the referring rules keep
  the same name and simply re-resolve upward — and warns that they have moved
  (`N reference(s) will re-resolve from … (inheritance collapse)`).
- A merge that drops tags or a description the survivor lacks now warns, naming
  any **dynamic address-group** whose membership a lost tag would change. Values
  still gate; attributes only warn.

## v1.5.1 — 2026-07-10

### Docs

- Documented the recent features on the docs site: `show` / `find object
  --expand` (open an object), `group edit-member`, and the workbench `v`
  (inspect) / `G` (add-to-group) spokes and dynamic, dropdown-driven create
  form. Refreshed the home-page command table and corrected the stale "alpha
  0.x" stability note (the project is past 1.0 and follows SemVer).
- Synced `uv.lock` to the current package version.

## v1.5.0 — 2026-07-10

### Added

- **`psc group edit-member` — idempotent group-membership edits** — add or
  remove one member of an **address-group** or **service-group**, the group
  analogue of `rule edit-member`. Renders delete-field + re-set (PAN-OS `set` on
  a member field appends), so every op is idempotent. `--kind` disambiguates a
  name that is both an address- and service-group; `--location` a name in
  several scopes. A dynamic (filter-based) address-group has no static member
  list and is rejected. Same dry-run-by-default + `--apply` gate as every other
  mutation.
- **Workbench: add the selection to a group (`G`)** — pick objects in the hub,
  press `G`, name a target address-/service-group, and each selected object is
  added to its membership (idempotent, over the same engine). Add-only in the
  TUI; removal lives in `psc group edit-member --remove`.

### Changed

- **Workbench create form is now dynamic** — the create spoke (`c`) shows only
  the fields the selected kind actually uses (e.g. `type`/`value` for an address,
  `members`/`filter` for an address-group, `color`/`comments` for a tag),
  updating live as you change the kind, instead of presenting every field at
  once.

## v1.4.0 — 2026-07-10

### Added

- **Workbench create: predefined fields are now dropdowns** — the create spoke's
  **type** (address: `ip-netmask`/`ip-range`/`ip-wildcard`/`fqdn`), **protocol**
  (service: `tcp`/`udp`), and **color** (tag: `color1`..`color42`, optional)
  fields were free-text Inputs you could fill with an invalid value; they are now
  `Select` dropdowns, so only valid values can be chosen. `crud` remains the
  validator — the dropdowns just prevent typos up front. Value/name/port/member
  fields stay free-text.

## v1.3.1 — 2026-07-10

### Fixed

- **Workbench inspect (`v`): start with nested groups collapsed** — the inspect
  tree previously force-expanded every node, dumping a deep group's entire
  subtree at once and drawing an expand arrow on leaf rows that couldn't expand.
  Now the opened object is the tree root (no redundant duplicate heading) with
  its direct members shown; nested groups start collapsed (drill in with
  enter/click) and true leaves render without an arrow.

## v1.3.0 — 2026-07-09

### Added

- **Open an object to see what it contains** (#136) — a new inspect capability
  over one engine (`psc/core/inspect.py`), exposed three ways:
  - `psc show <name>` and `psc find object <name> --expand/-x` — expand any
    object by name. Addresses/services print their value; address-groups and
    service-groups expand recursively into a member **tree** plus
    **`effective_leaves`** (the deduped, flattened leaf addresses/ports the
    object resolves to). Tags list every carrier; rules group resolved
    `source`/`destination`/`service` members by field.
  - **Workbench TUI**: press `v` on a search result to open a read-only inspect
    spoke showing the same tree + effective-leaf set.

  Member resolution reuses the reference graph (PAN-OS name shadowing, dynamic
  address-group membership, cycle-safe). Unresolvable members are always shown
  and flagged — `dynamic` filters, `dangling` references, and `cycle`s each mark
  their node, and `effective_complete: false` (plus a stderr warning) signals a
  partial flat set. Pure read: nothing is staged, mutated, or written.
  Backward-compatible — `find object` without `-x` is unchanged.

## v1.2.0 — 2026-07-02

### Added

- **Workbench: send a duplicate-scan bucket to the selection** (#131) — the
  config-wide duplicate scan spoke (`D`) was pure discovery with no way to act
  on a result. Highlight a bucket and press `space` to push its members onto the
  hub selection (idempotent, kind-aware), so a scan result flows straight into
  any downstream spoke (`d` merge, `a` audit, `x` decommission, …). The scan
  itself still mutates nothing — merging stays in the `d` spoke / `dedup merge`
  CLI. Backward-compatible feature addition.

## v1.1.2 — 2026-07-02

### Fixed

- **Workbench: `set`-script member lists were swallowed in plan previews** (#129)
  — Textual's console-markup engine treats `[ ... ]` as a tag, so a rendered
  `set … destination [ addr-a addr-b ]` showed as an empty `… destination` with
  the members eaten. Display-only: the emitted `set` script / config file was
  always correct. The plan preview (dedup/move/create/… review panel), the
  staged-changelist detail, and the apply-time set preview now render the member
  lists literally.

## v1.1.1 — 2026-07-02

### Changed

- **Workbench apply is now reachable only from the staged changelist** (#127) —
  the hub-level `ctrl+a` apply binding is removed. Open the staged spoke (`s`),
  then `ctrl+a` there opens the apply screen. This forces a review of the exact
  staged batch before it can be emitted (offline write or live push), narrowing
  the blast radius of a stray keypress. No engine change — the apply screen and
  its confirmations are unchanged; only its entry point moved.

## v1.1.0 — 2026-07-02

Workbench (TUI) parity + UX release. Every change is in the interactive
workbench; the CLI surface, the JSON output contracts, and the exit codes are
unchanged, so this is a backward-compatible minor bump.

### Added

- **Workbench discovery spokes** (#95) — the TUI now reaches the config-wide
  *discovery* commands the selection-scoped spokes never covered:
  - `D` **duplicates scan** — every duplicate bucket in the whole config, with a
    kind toggle for addresses / services / address-groups (reuses
    `find_duplicate_addresses` / `find_duplicate_services` / `find_duplicate_groups`).
  - `f` **diff** — device-group-vs-device-group drift (added/removed/changed),
    mirroring `psc diff --device-group A --against B`.
  - `o` **export** — write objects of one kind to an NDJSON file, mirroring
    `psc export`; a read-only export that never overwrites the source config.
  - the `a` **audit** spoke gained a mode toggle for the
    `audit services-vs-wellknown` scan alongside address overlaps.
- **Interactive apply-time output picker** (#122) — `ctrl+a` now opens an apply
  screen where you choose the output format + destination *after* seeing the
  staged batch, instead of committing to it at launch. Options: print the set
  script, save a `.set` file, save a full or minimal-partial XML config, or push
  to the live candidate (offered only for live sessions). The `--output-mode` /
  `--apply-out` launch flags still work — they just pre-seed the default now. A
  live push and an overwrite of an existing file each require an explicit second
  confirmation. Same safety model (blocker gate, never overwrite the source,
  never commit live).
- **Switch the active source from within the workbench** (#121) — the profiles
  spoke can now `ctrl+r` reload the running session onto a different profile (a
  live connection) or an offline export path, without relaunching.
  `WorkbenchSession.reload(source)` rebuilds the working snapshot and discards
  the selection + staged batch, so it asks for a second `ctrl+r` to confirm when
  a batch is staged; a live connection error is surfaced, not crashed.

## v1.0.0 — 2026-07-02

First stable release. From this version `panorama-super-cli` follows
[Semantic Versioning](https://semver.org/): the CLI surface, the JSON output
contracts, and the exit codes are public API and won't change incompatibly
without a major bump.

### Added

- **`psc find ip --resolve-fqdn`** (#3) — opt-in DNS. FQDN address objects are
  resolved (cached, timeout-bounded) and match when their A/AAAA records include
  the queried IP. The offline default never touches DNS, so hermetic/CI runs are
  unchanged; objects whose lookup fails are skipped with a count on stderr.
- **`psc dedup merge --group <value> [--keep <name>]`** (#4) — collapse an
  ENTIRE duplicate-address bucket (every object sharing that value, as listed by
  `dedup addresses`) toward one survivor in a single plan, alongside the existing
  pairwise `--keep/--remove`. `--keep` picks the survivor (defaults to the first
  bucket member); `--group` and `--remove` are mutually exclusive; `--not-strict`
  matches the bucket under host-bit masking. Same repoint-before-delete engine and
  value-change gate as pairwise merge.
- **`psc name apply --all`** (#15) — bulk reference-aware rename-to-scheme. Renames
  every non-compliant object (everything `name lint` reports) to its scheme name
  in one reviewed, all-or-nothing plan, blocking any rename that would collide or
  shadow. `name apply` now takes exactly one of `--object NAME` or `--all`.
- **`psc move … --cascade`** (#76) — promote an object's transitive DG-local
  dependency closure (group members, tags) to the destination in one deepest-first
  ordered plan. Without `--cascade`, an unresolved dependency still blocks the move
  and is listed to promote first; a dependency also needed by an object left behind
  is promoted but its source copy is retained (with a warning).
- **`psc diff a.xml b.xml`** and **`psc diff --device-group A --against B`** (#13)
  — a pure-read drift report. File mode compares two exported configs (pre/post
  review); DG mode compares the effective visible object sets of two device-groups
  in one config. Reports added/removed/changed objects, groups, and rules, grouped
  by kind. A difference is data, so it exits `0` even when the sides differ.
- **`psc export <kind>`** and **bulk import via `psc set <kind> -f objs.ndjson`**
  (#14) — object portability as NDJSON (one canonical JSON object per line, ordered
  by `(location, name)`). `export` covers addresses / address-groups / services /
  service-groups / tags; `set -f` plans the whole file as one reviewable
  `ChangeSet` (the same crud validation, aggregated), through the identical
  dry-run-default + `--apply` gate — one blocker refuses the whole file.
- **`psc refs unused --ignore-disabled`** (#9) — treat disabled rules as
  non-references, surfacing objects used *only* by disabled rules (a cleanup target
  once a rule set is retired).
- **`psc audit services-vs-wellknown`** (#11) — flag custom service objects whose
  single destination port duplicates a predefined PAN-OS service (e.g.
  `service-http`) or an IANA well-known port (e.g. `ssh`). The `kind` column
  distinguishes a real predefined object from a bare well-known port number; ranges
  and multi-port objects are never flagged. Pure read; honours device-group scope.
- **`psc refs unused --caveat/--no-caveat`** (#56) — the scan-scope blind-spot
  notice (printed to stderr so stdout stays pure machine output) is now
  suppressible with `--no-caveat` for callers who've internalised the coverage
  limits.
- **Workbench TUI now at full CLI parity** — the interactive `psc workbench`
  (alias `psc w`) shipped in v0.5.0 with seven spokes; it now covers every engine.
  New spokes: **create** (`c`, object creation), **profiles** (`p`, persisted
  profile CRUD), **refs-unused** (`i`), **dangling** (`g`), **name-lint** (`l`),
  **name-apply** (`n`), plus a **staged-changelist** view (`s`, inspect one
  change's full set-script and drop a single change without discarding the batch)
  and direct **remove-from-selection** (`delete`/`backspace`). The dedup spoke
  gained a whole-bucket merge with a survivor picker; move gained a destination
  drop-down; rename/name-apply let you choose the entry. Output modes — SET
  (preview or write the script to a file), OFFLINE_APPLY (full or partial config
  via `--apply-out`), LIVE_APPLY (candidate push, never commits) — apply the whole
  staged batch at once (`ctrl+a`) under the same safety model as the CLI.

### Security

- **Security hardening and `SECURITY.md`** (#78). A v1.0.0 security review found
  no High/Critical issues; the code-level findings are addressed. Every GitHub
  Actions `uses:` is pinned to a full commit SHA (Dependabot keeps them current);
  CI workflows declare least-privilege `permissions`. `~/.psc/config.yaml` (which
  may hold an API key) is now created `0600` **atomically** (via
  `os.open(..., O_CREAT|O_TRUNC, 0o600)`, no world-readable window) with the parent
  `~/.psc` at `0700`; a pre-existing looser file is repaired on write. A new
  **`PSC_API_KEY`** environment variable overrides the profile's stored key
  (precedence env > file), keeping the secret off disk. Running a profile with
  `--insecure` (`verify_ssl=false`) now emits a loud `InsecureTLSWarning` on every
  live connection — especially the password-bearing key-fetch — since credentials
  would then cross the wire MITM-able. A security disclosure policy is documented
  in `SECURITY.md`.

### Documentation

- New **Workbench** guide and a **Comparing and porting configs** guide (diff +
  NDJSON export/import), both added to the docs nav. Existing guides and the CLI
  reference updated for every new command/flag; the bundled Agent Skill and README
  reflect the full v1.0.0 surface.

## v0.5.0 — 2026-07-01

### Added

- **`psc workbench` (alias `psc w`) — an interactive Textual TUI** (#80). A
  keyboard-driven "workbench" that glues the engines together around a
  persistent, heterogeneous **selection buffer**: search objects (by IP, value,
  or name), multi-select them, then route the selection into a spoke. Mutations
  accumulate into a git-like **staged changelist** that compounds in-memory (via
  the existing `apply_changeset`) and applies as one batch — `set` script,
  offline write, or live candidate push (never commits). All seven v1 spokes are
  included:
  - **dedup** (`d`) — merge duplicate selected addresses;
  - **usage** (`u`) — where-used for the selection (read-only);
  - **audit** (`a`) — address overlaps/containment involving the selection
    (read-only);
  - **move** (`m`) — promote selected objects toward `shared`;
  - **decommission** (`x`) — reference-safe cascading teardown of selected
    addresses;
  - **rename** (`r`) — reference-aware rename;
  - **rule** (`e`) — add selected objects as members of an existing rule field.

  The TUI is a pure `psc.core`/`psc.output` frontend (it never imports
  `psc.cli`), inheriting every safety invariant: dry-run by default, a hard
  blocker gate on staging, repoint-before-delete, offline never overwrites the
  source export, and live never commits. Textual is now a runtime dependency.

### Notes

- The **rule** spoke edits members of *existing* rules; rule creation is not yet
  supported (there is no core rule-creation engine).

## v0.4.3 — 2026-06-08

### Fixed

- **`refs unused` / `refs used` now resolve dynamic address-group (DAG)
  membership from config tags** (#60) — a new pure evaluator
  (`psc/core/dagfilter.py`) parses a DAG's tag filter (`and`/`or`/`not`,
  parentheses, single-quoted tags) and matches it against the static tags psc
  already parses. An address whose only use is being tag-matched into a
  rule-referenced DAG is no longer reported `unused` (deleting it would have
  silently dropped a host from that rule), and `refs used <addr>` now surfaces
  the DAG → rule path (as a `dynamic` referrer). A DAG matches only addresses
  visible from its own scope (its device-group, ancestors, and `shared`). An
  unparseable filter is matched-nothing and warned about on stderr (naming the
  DAG) rather than crashing the audit or being guessed at.

### Known limitation

- DAG membership from **externally registered IPs** (XML-API / User-ID /
  VM-info) is runtime state absent from the config export and is still not
  covered; resolving it requires a live op-command query and is tracked as a
  follow-up. The `refs unused` stderr caveat and the *Coverage and blind spots*
  guide reflect this.

## v0.4.2 — 2026-06-08

### Added

- **`psc move <kind> <name> --from <loc> --to <loc>`** (#74) — promote an
  object (`address`/`address-group`/`service`/`service-group`/`tag`) from a
  device-group toward `shared`. Restricted to the safe direction: `--to` must be
  `shared` or an *ancestor* of `--from`, where references fall through to the
  destination with no repoint. Blocks (exit `6`) on a sibling/child/unrelated
  destination (would orphan references), an intermediate device-group that
  already defines the name (a shadow), the object's own dependencies
  (members/tags) not being visible at the destination, or a collision with a
  different-valued object already there. A collision with an identical-valued
  object simply drops the source copy. Dry-run by default; offline `--apply`
  round-trips through the rewritten config.

## v0.4.1 — 2026-06-08

### Fixed

- **`psc dedup addresses|services|groups`** (#72) — the table view now draws a
  horizontal rule between each group of duplicates, so the blocks are easy to
  tell apart at a glance. Machine formats (json/jsonl/yaml/csv) are unchanged.

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
