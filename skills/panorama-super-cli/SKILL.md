---
name: panorama-super-cli
description: Use when the user asks to find, audit, deduplicate, merge, rename, or clean up Palo Alto Panorama address/service objects, groups, or tags via `psc`. Resolves IPs to objects, finds duplicates, and rewrites referencing rules safely.
when_to_use: The user has `psc` installed (`uv tool install panorama-super-cli`) and asks anything about Panorama object hygiene — "is this IP already an object", "find duplicate objects", "merge these two objects", "what rules use this object", "rename per our convention", "what objects are unused". Also use when debugging an automation that calls `psc`.
---

# panorama-super-cli (`psc`)

`psc` manages Palo Alto **Panorama** objects (address, address-group, service,
service-group, tag) and rewrites the groups/rules that reference them — safely.
It works **offline** against an exported config (`--config file.xml`) or **live**
against Panorama (a configured profile). Writes are **dry-run by default**.

## The two rules that matter most

1. **Everything is dry-run until `--apply`.** Mutating commands (`dedup merge`,
   `name rename`, `name apply`) print a plan and exit `0` without changing
   anything. Read the plan, then re-run with `--apply`.
2. **Always pass `-o json`** for machine use. Stdout becomes a stable JSON
   document; errors come back as a typed envelope. (A non-TTY stdout
   auto-switches to JSON anyway, but be explicit.)

## Command shape

```
psc [GLOBAL OPTS] <group> <command> [ARGS] [WRITE OPTS]
```

- **Global (context) options go *before* the group:** `-c/--config FILE`,
  `-p/--profile NAME`, `-o/--output FMT`, `-d/--device-group NAME`, `--strict`,
  `--debug`.
- **Write-execution options go *after* the command:** `--apply`, `--out PATH`,
  `-of/--output-format xml|set`.

```bash
psc -c panorama.xml -o json find ip 10.0.0.10
psc -c panorama.xml dedup merge --keep h-web1 --remove web-primary --apply --out fixed.xml
```

## Pick a source

- **Offline:** `-c panorama.xml` — an exported config. Read-only against the
  device; `--out PATH` writes a *new* file (it never overwrites the source
  export). Choose what that file holds with `-of/--output-format`:
  - `xml` (default) — the whole config rewritten, loadable with `load config`.
  - `set` — just the equivalent PAN-OS `set` script (the creates/deletes/
    repoints that achieve the change). Easier to read and to paste into a
    config session or `load config partial`. Use this when a human reviews the
    change or you want a minimal, diff-like artifact; use `xml` when you need a
    full, directly-loadable replacement config.

  ```bash
  psc -c cfg.xml dedup merge --keep a --remove b --out plan.set -of set
  ```
  `--out` is an *artifact* request, not a mutation: it's honoured even in a
  dry-run (writing a file never touches the source export). `-of` shapes that
  file; for the script on stdout instead, use `-o set`.
- **Live:** `-p prod` — fetches the running config over the XML API. `--apply`
  pushes the plan to Panorama's **candidate** config and never commits; you
  review and commit yourself. `--out` still works on a live profile to save a
  reviewable `set`/`xml` artifact (with or without `--apply`) without touching
  the device — handy for capturing the script before you push.

## The commands

### find — is this IP an object?

```bash
psc -c cfg.xml -o json find ip 10.0.0.10          # exact/contains/within + groups
psc -c cfg.xml -o json find ip -e 10.0.0.10       # exact only (10.0.0.10 == /32)
psc -c cfg.xml -o json find ip 10.0.0.0/24        # everything inside the /24
psc -c cfg.xml -o json find ip -f ips.txt         # a whole list (array result)
psc -c cfg.xml -o json find ip 1.2.3.4 --resolve-fqdn   # opt-in DNS: match FQDN objects too
psc -c cfg.xml -o json find object grp-web        # locate by exact name
psc -c cfg.xml -o json find object grp-web -x      # OPEN it: member tree + effective leaves
```

`exists: true` means there's an exact-match object. `matches[].match` is one of
`exact` (equal), `contains` (object is broader), `within` (object is narrower).
Pass `--exact`/`-e` to keep only `exact` matches — handy when a broad object
like `10.0.0.0/8` would otherwise drown out the host you asked for.
`--resolve-fqdn` opts into DNS: FQDN objects are resolved and match when their
A/AAAA include the IP (cached, timeout-bounded; failures counted on stderr). The
default never touches DNS — leave it off for hermetic/offline runs.

### show — open an object to see what it contains

```bash
psc -c cfg.xml show grp-web                        # tree + effective leaf addresses
psc -c cfg.xml -o json show grp-web                # ObjectView model (tree, effective_leaves, …)
psc -c cfg.xml find object grp-web -x              # identical — `show` is the alias
```

`show <name>` (a.k.a. `find object <name> --expand/-x`) expands any object by
name. A plain address/service prints its value; an **address-group** or
**service-group** expands recursively into a member `tree` plus
`effective_leaves` — the deduped, flattened set of leaf addresses/ports it
resolves to. A **tag** lists every object carrying it; a **rule** groups its
resolved `source`/`destination`/`service` members by field. Unresolvable members
are shown and flagged, never dropped: dynamic filters (`dynamic`), dangling
references (`dangling`), and nested cycles (`cycle`) each mark their node, and
`effective_complete: false` (with a stderr warning) signals the flat set is
partial. Pure read — nothing is staged or written.

### dedup — duplicates and merging

```bash
psc -c cfg.xml -o json dedup addresses            # strict: byte-identical values only
psc -c cfg.xml -o json dedup addresses --not-strict  # also mask host bits (10.1.1.50/24 ~ 10.1.1.0/24)
psc -c cfg.xml -o json dedup services
psc -c cfg.xml -o json dedup groups               # address-groups w/ identical effective member set
psc -c cfg.xml -o json dedup merge --keep h-web1 --remove web-primary   # dry-run plan (pairwise)
psc -c cfg.xml dedup merge --keep h-web1 --remove web-primary --apply --out fixed.xml
psc -c cfg.xml dedup merge --group 10.0.0.10/32 --keep h-web1 --apply --out fixed.xml  # whole bucket
psc -c cfg.xml dedup merge-group --keep grp-a --remove grp-b --apply --out fixed.xml
```

`merge` repoints **every** group/security-rule/NAT reference onto `--keep`
*before* deleting `--remove`. It **blocks** (exit `6`) on a value mismatch
(use `--allow-value-change` to override) or if the survivor isn't visible where
a reference lives. Per-object locations: `--keep-location` / `--remove-location`
(default: `--device-group` or `shared`).

Visibility is judged against the config **as the plan leaves it**. Collapsing a
device-group's local copy into an identical `shared` object of the same name is
allowed: the copy is deleted and the device-group's rules re-resolve upward to
the survivor, so the plan carries only the delete (`psc` warns that the
references moved). It still blocks when an *intermediate* device-group carries
the same name and would intercept that upward walk — merge that copy too. Merges
that drop a tag the survivor lacks warn, naming any dynamic address-group whose
membership the lost tag would change.

`--group <value>` collapses the WHOLE duplicate-address bucket sharing that value
(from `dedup addresses`) toward one survivor in a single plan — `--keep` picks the
survivor, defaulting to the **most visible** member (`shared`, else the
device-group nearest the root, skipping any the other members' rules could not
resolve); `--group` and `--remove` are mutually exclusive.

`dedup groups` buckets address-groups by the canonical leaf-address set they
expand to (nested groups flattened); dynamic/unresolvable groups are skipped and
noted on stderr (not exhaustive). `--location` scopes the comparison.
`merge-group` collapses `--remove` into `--keep` with the same repoint-before-
delete engine, but has **no value-change override** — it **blocks** (exit `6`)
unless both groups expand to the *same* set, on a nested/cyclic pair, or if the
survivor isn't visible where a reference lives.

### refs — where-used, unused, dangling

```bash
psc -c cfg.xml -o json refs used h-web1            # delete/rename pre-flight
psc -c cfg.xml -o json refs unused --kind address # recursive: nothing a rule reaches
psc -c cfg.xml -o json refs unused --kind address --ignore-disabled  # only-disabled-rule users
psc -c cfg.xml -o json refs unused --kind address --no-caveat        # suppress the stderr caveat
psc -c cfg.xml -o json refs dangling              # references to missing objects
```

`--ignore-disabled` treats disabled rules as non-references (surfaces objects used
*only* by disabled rules). `refs unused` prints the blind-spot caveat on stderr by
default; `--no-caveat` silences it (stdout is unaffected).

`refs used` may need `--kind` and `--location` if a name is ambiguous. Coverage
spans groups and **every** object-referencing rulebase — security, NAT, PBF,
decryption, authentication, QoS, application-override, DoS, SD-WAN,
tunnel-inspect, network-packet-broker — so `unused` never reports an object that
only a non-security rule reaches. A `referrer_kind` like `qos-rule` or
`pbf-rule` in the output tells you exactly which rulebase points at the object.

> **⚠️ `unused` = unused *by policy*, NOT *safe to delete*.** psc parses only
> device-group objects + policy rulebases. It does **not** see: templates &
> network/device config (IKE/IPSec, GlobalProtect, service routes, log servers,
> static routes), dynamic-address-group membership from **externally registered
> IPs** (config-tag DAG membership *is* resolved), or
> profiles/schedules/EDLs/regions/applications. Any object referenced only
> there is falsely reported `unused`. **Never auto-delete on an `unused` result
> — surface it as a candidate and have a human verify in Panorama**, especially
> `shared` objects. `merge`/`rename` are safe (they block when a reference
> can't be repointed); **deletion is the unprotected operation.**

### name — opt-in naming templates

```bash
psc -c cfg.xml -o json name lint                  # drift vs the configured scheme
psc -c cfg.xml name rename --object h-web1 --to H-10.0.0.10   # reference-aware
psc -c cfg.xml name apply --object h-web1          # rename to the scheme's name
psc -c cfg.xml name apply --all                    # rename EVERY drifting object (one plan)
```

Rename **refuses** a shared-vs-device-group shadow collision (exit `6`).
`name apply` takes exactly one of `--object NAME` or `--all`; `--all` renames every
non-compliant object in one reference-aware plan, blocking any collide/shadow.

### audit — overlaps + well-known ports

```bash
psc -c cfg.xml -o json audit overlaps             # pairs where one range contains/overlaps another
psc -c cfg.xml --strict audit overlaps            # exit 5 when none (CI gate)
psc -c cfg.xml -o json audit services-vs-wellknown   # custom services duplicating a predefined/IANA port
```

Pure reads. `overlaps`: each pair once; `relationship` is `contains` (one broader)
or `overlaps`; `ip-netmask`/`ip-range` only. `services-vs-wellknown`: custom
services whose single dest port matches a predefined PAN-OS service or IANA
well-known port (the `kind` column tells them apart). Scope with `-d`; `--strict`
is the global flag (before the group).

### set — create / update one object

```bash
psc -c cfg.xml set address --name h-web1 --type ip-netmask --value 10.0.0.10/32
psc -c cfg.xml set address-group --name grp-web --member h-web1 --member h-web2   # OR --filter "'prod'"
psc -c cfg.xml set service --name tcp-8443 --protocol tcp --dest-port 8443
psc -c cfg.xml set service-group --name svc-web --member tcp-443 --member tcp-8443
psc -c cfg.xml set tag --name prod --color color5 --comments "prod"
psc -c cfg.xml set address --name h-web1 --type ip-netmask --value 10.0.0.11/32 --apply --out updated.xml  # update = offline only
```

Dry-run by default. PAN-OS validation: name ≤63 leading-alnum (tag ≤127), desc
≤255, address exactly one `--type`/`--value`, service needs a dest (or source)
port, tag `--color` is `color1..color42` (note: tag uses `--comments`, not
`--description`). Validation error → exit `4`. **Blockers** (exit `6`):
cross-kind name collision; an in-place value-type or static↔dynamic mode change
on update. Live `--apply` **only creates** — to *update* an existing object use
offline `--apply --out`.

### rule edit-member — idempotent rule-field edits

```bash
psc -c cfg.xml rule edit-member --rule allow-web --field source --add h-web1
psc -c cfg.xml rule edit-member --rule allow-web --field source --remove h-old
psc -c cfg.xml rule edit-member --rule allow-web --field service --add tcp-8443 --rulebase post
```

Exactly one of `--add`/`--remove`; `--field` is source|destination|service|
application; `--rulebase` defaults `pre`. Removal renders delete-field + re-set
(PAN-OS `set` on a member field appends), so every op is idempotent. NAT
`service` is scalar → **blocked**; `application` on a non-security rule →
validation. Unknown rule → exit `5`; ambiguous → exit `4`.

### group edit-member — idempotent group-membership edits

```bash
psc -c cfg.xml group edit-member --group web-pool --add web-srv-09
psc -c cfg.xml group edit-member --group web-pool --remove web-srv-02
psc -c cfg.xml group edit-member --group dup --add x --kind service-group
```

The group analogue of `rule edit-member`: add/remove one member of an
**address-group** or **service-group**, idempotently (delete-field + re-set, so
re-running is a no-op). Exactly one of `--add`/`--remove`. The group is resolved
by name; `--kind` (address-group|service-group) disambiguates a name that is
both, `--location` a name in several scopes. A **dynamic** (filter-based)
address-group has no static member list → validation error. Unknown group → exit
`5`; bad input/ambiguous → exit `4`. To *create* a group (not edit members) use
`set address-group` / `set service-group`.

### decommission — reference-safe teardown

```bash
psc -c cfg.xml -o json decommission 10.1.0.5             # dry-run plan
psc -c cfg.xml decommission 10.1.0.0/24 --apply --out torn-down.xml
psc -c cfg.xml decommission -f retired-ips.txt --scope DG-EDGE
```

Targets: positional args, repeated `--target`, and/or `-f/--file` (one per line,
`#` comments). Tears down address objects matching the IP/CIDR/range in order:
scrub groups → scrub rules → delete orphaned rules (empty source OR destination;
`any` survives) → delete emptied groups → delete the objects, cascading to a
fixpoint. **Only EXACT + WITHIN matches** are removed (a broader containing
object is left in place). `--keep-groups`/`--keep-rules` stop short of deleting
those. **Blocks** (exit `6`) on NAT-translation/PBF-next-hop references and
DAG-filter-tag matches; orphan-rule deletions are warnings. This is the safe
teardown path — prefer it over hand-scrubbing then `refs unused` + manual delete.

### move — promote an object toward shared

```bash
psc -c cfg.xml -o json move address h-web1 --from DG-EDGE --to shared   # dry-run plan
psc -c cfg.xml move address h-web1 --from DG-EDGE --to shared --apply --out promoted.xml
psc -c cfg.xml move address-group grp-web --from DG-CHILD --to DG-PARENT   # to an ancestor DG
```

Relocates one object (`address`/`address-group`/`service`/`service-group`/`tag`)
from a device-group toward `shared`. **Only promotes** — `--to` must be `shared`
or an *ancestor* of `--from`; that is the direction where references fall through
to the destination automatically, so **no repoint is ever needed**. **Blocks**
(exit `6`) on: a sibling/child/unrelated destination (would orphan references); a
device-group between source and destination that already defines the name (a
shadow); the object's own dependencies (group members, tags) not being visible at
the destination — move those first; or a collision with a *different-valued*
object already at the destination. A collision with an *identical-valued* object
simply drops the source copy (references resolve to the destination). Single
object per run; dry-run by default.

Add `--cascade` to also promote the object's transitive DG-local dependencies
(group members, tags) to the destination in one deepest-first plan; without it an
unresolved dependency **blocks** the move and is listed to move first.

### diff — what changed between two configs (or two DGs)

```bash
psc diff before.xml after.xml -o json             # file-vs-file (pre/post review)
psc -c cfg.xml diff --device-group A --against B   # DG-vs-DG effective object sets
```

Pure read: added/removed/changed objects, groups, rules. The two modes are
mutually exclusive. A difference is *data*, so it **exits 0** even when the sides
differ — branch on the JSON, not the exit code.

### export / set -f — port objects as NDJSON

```bash
psc -c src.xml export addresses --out addrs.ndjson       # dump one kind as NDJSON (PLURAL kind)
psc -c dst.xml set address -f addrs.ndjson               # bulk import (dry-run plan; SINGULAR subcommand)
psc -c dst.xml set address -f addrs.ndjson --apply --out merged.xml
```

`export <kind>` takes a PLURAL kind (addresses|address-groups|services|
service-groups|tags) and writes one JSON object per line, ordered by
(location, name). `set <kind> -f <file>` uses the SINGULAR `set` subcommand
(address|address-group|service|service-group|tag) and imports the whole batch as
**one** reviewable ChangeSet — same crud validation, aggregated; one blocker
refuses the whole file; the singular flags are ignored in this mode.

### profile — live connections

```bash
psc init --name prod --host panorama.example.com --user admin   # password→API key, verify, save (0600)
psc login                                                       # verify the stored key (show system info)
psc login --user admin                                          # rotate the key (re-keygen + verify)
psc profile add --name prod --host panorama.example.com --api-key "$KEY" --default  # scriptable, key in hand
psc profile list
```

Password comes from `$PSC_PASSWORD` or a hidden prompt, never a flag. Set
`$PSC_API_KEY` to override the stored key so the secret never hits disk
(precedence: env > config file). TLS is verified by default; add `--insecure` to
`init` for a self-signed Panorama — it emits a loud warning on every live
connection (never use it against production). Auth failures exit `8`, unreachable
host exits `7`.

### workbench — interactive TUI

```bash
psc -c cfg.xml workbench                          # offline
psc -p prod w --output-mode live-apply            # live (never commits)
```

`psc workbench` (alias `psc w`) is a keyboard-driven TUI at full CLI parity:
search → multi-select into a buffer → route into a spoke → stage plans into a
git-like changelist → review it in the staged spoke (`s`) and `ctrl+a` there
opens an apply screen to choose the output at apply time: a `set` script (inline
or `.set` file), a full or minimal-partial offline config write, or a live
candidate push (live sessions only). Apply is reachable only from the staged
changelist, so a batch is always reviewed before it's emitted. The
`--output-mode` / `--apply-out` launch flags just pre-seed that default. Same
dry-run/stage, blocker gate, and repoint-before-delete safety as the CLI.
Alongside the
selection-scoped action spokes are config-wide *discovery* spokes: `D`
duplicates scan, `f` device-group diff, `o` NDJSON export, and a well-known-port
mode on the `a` audit spoke. `v` opens a read-only inspect view of the focused
object (member tree + effective leaves); `G` adds the current selection as
members of a named group, and `N` builds a **new** group out of the selection
(kind derived from what's selected; the location picker defaults to the narrowest
location that sees every member, and a member the group's location can't see — or
whose name is shadowed there — is a blocker); `c` opens the create form, whose
fields adapt to the chosen kind (predefined values — address type, service
protocol, tag color — are dropdowns). For scripting/agents prefer the one-shot commands above; the
workbench is for interactive sessions.

## Output formats

`-o table|json|jsonl|yaml|csv|set`. Use `json` for agents. `-o set` on a plan
command emits PAN-OS `set` commands (member edits render as `delete` + `set`, so
they're idempotent, not additive) — paste them into a config session or
`load config partial`.

## The error + exit-code contract (stable; branch on it)

Errors print a JSON envelope `{"error","type","details"}` — to stdout under
`-o json`, else stderr.

| Exit | type | Meaning |
| --- | --- | --- |
| 0 | — | success |
| 1 | internal | bug |
| 2 | — | CLI usage error (bad flags) |
| 3 | input | bad config/file |
| 4 | validation | bad input (invalid IP, ambiguous name) |
| 5 | not_found | nothing found (with `--strict`) |
| 6 | conflict | plan blocked/unsafe — see `details.blockers` |
| 7 | transport | live API connection failed |
| 8 | auth | live API auth failed |
| 9 | config | profile/config problem (incl. no source given) |

## Recommended agent flow for a safe edit

```bash
# 1. Plan as data and check it isn't blocked.
plan=$(psc -c cfg.xml -o json dedup merge --keep a --remove b)
echo "$plan" | jq -e '.blockers | length == 0' >/dev/null || { echo "blocked"; exit 1; }
# 2. Apply to a new file, then load it into Panorama yourself and commit.
psc -c cfg.xml dedup merge --keep a --remove b --apply --out fixed.xml
```

**Tearing down an object?** Don't hand-scrub groups/rules then guess at
`refs unused`. Use `decommission <ip|cidr>` — it plans the whole reference-safe
cascade (groups → rules → orphaned rules → emptied groups → objects) and blocks
on anything it can't safely rewrite. Same dry-run-then-`--apply --out` flow.

## What NOT to do

- DO NOT add `--apply` to a read command by reflex (ignored on reads, dangerous habit).
- DO NOT parse the `table` format — use `json`/`jsonl`.
- DO NOT apply a plan whose `blockers` is non-empty — fix the cause.
- REMEMBER `--out` only ever writes a file; it never pushes. On a live profile
  use `--apply` to push the candidate (add `--out` too if you also want the
  artifact saved). And `psc` never commits — you do.
- DO NOT put `--apply`/`--out` before the subcommand, or `-c`/`-o` after it.

## When something fails

1. Check the exit code (`echo $?`) and the envelope `type`.
2. For a blocked plan (exit 6), read `details.blockers` — it names the exact
   unsafe reference.
3. Re-run with `--debug` for structured logs on stderr.
