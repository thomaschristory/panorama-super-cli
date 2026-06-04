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
psc -c cfg.xml -o json find object grp-web        # locate by exact name
```

`exists: true` means there's an exact-match object. `matches[].match` is one of
`exact` (equal), `contains` (object is broader), `within` (object is narrower).
Pass `--exact`/`-e` to keep only `exact` matches — handy when a broad object
like `10.0.0.0/8` would otherwise drown out the host you asked for.

### dedup — duplicates and merging

```bash
psc -c cfg.xml -o json dedup addresses            # strict: byte-identical values only
psc -c cfg.xml -o json dedup addresses --not-strict  # also mask host bits (10.1.1.50/24 ~ 10.1.1.0/24)
psc -c cfg.xml -o json dedup services
psc -c cfg.xml -o json dedup groups               # address-groups w/ identical effective member set
psc -c cfg.xml -o json dedup merge --keep h-web1 --remove web-primary   # dry-run plan
psc -c cfg.xml dedup merge --keep h-web1 --remove web-primary --apply --out fixed.xml
psc -c cfg.xml dedup merge-group --keep grp-a --remove grp-b --apply --out fixed.xml
```

`merge` repoints **every** group/security-rule/NAT reference onto `--keep`
*before* deleting `--remove`. It **blocks** (exit `6`) on a value mismatch
(use `--allow-value-change` to override) or if the survivor isn't visible where
a reference lives. Per-object locations: `--keep-location` / `--remove-location`
(default: `--device-group` or `shared`).

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
psc -c cfg.xml -o json refs dangling              # references to missing objects
```

`refs used` may need `--kind` and `--location` if a name is ambiguous. Coverage
spans groups and **every** object-referencing rulebase — security, NAT, PBF,
decryption, authentication, QoS, application-override, DoS, SD-WAN,
tunnel-inspect, network-packet-broker — so `unused` never reports an object that
only a non-security rule reaches. A `referrer_kind` like `qos-rule` or
`pbf-rule` in the output tells you exactly which rulebase points at the object.

> **⚠️ `unused` = unused *by policy*, NOT *safe to delete*.** psc parses only
> device-group objects + policy rulebases. It does **not** see: templates &
> network/device config (IKE/IPSec, GlobalProtect, service routes, log servers,
> static routes), dynamic-address-group membership, or
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
```

Rename **refuses** a shared-vs-device-group shadow collision (exit `6`).

### audit — overlapping / contained ranges

```bash
psc -c cfg.xml -o json audit overlaps             # pairs where one range contains/overlaps another
psc -c cfg.xml --strict audit overlaps            # exit 5 when none (CI gate)
```

Pure read. Each pair once; `relationship` is `contains` (one broader) or
`overlaps`. `ip-netmask`/`ip-range` only (no FQDN/wildcard). Scope with `-d`;
`--strict` is the global flag (before the group).

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

### profile — live connections

```bash
psc init --name prod --host panorama.example.com --user admin   # password→API key, verify, save (0600)
psc login                                                       # verify the stored key (show system info)
psc login --user admin                                          # rotate the key (re-keygen + verify)
psc profile add --name prod --host panorama.example.com --api-key "$KEY" --default  # scriptable, key in hand
psc profile list
```

Password comes from `$PSC_PASSWORD` or a hidden prompt, never a flag. TLS is
verified by default; add `--insecure` to `init` for a self-signed Panorama. Auth
failures exit `8`, unreachable host exits `7`.

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
