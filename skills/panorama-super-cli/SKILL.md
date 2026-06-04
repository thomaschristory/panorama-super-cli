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
- **Write-execution options go *after* the command:** `--apply`, `--out PATH`.

```bash
psc -c panorama.xml -o json find ip 10.0.0.10
psc -c panorama.xml dedup merge --keep h-web1 --remove web-primary --apply --out fixed.xml
```

## Pick a source

- **Offline:** `-c panorama.xml` — an exported config. Read-only against the
  device; `--apply` writes a *new* file via `--out`.
- **Live:** `-p prod` — fetches the running config over the XML API. Reads only
  in v0.1 (writes land in v0.2).

## The commands

### find — is this IP an object?

```bash
psc -c cfg.xml -o json find ip 10.0.0.10          # exact/contains/within + groups
psc -c cfg.xml -o json find ip 10.0.0.0/24        # everything inside the /24
psc -c cfg.xml -o json find ip -f ips.txt         # a whole list (array result)
psc -c cfg.xml -o json find object grp-web        # locate by exact name
```

`exists: true` means there's an exact-match object. `matches[].match` is one of
`exact` (equal), `contains` (object is broader), `within` (object is narrower).

### dedup — duplicates and merging

```bash
psc -c cfg.xml -o json dedup addresses            # same value, different names
psc -c cfg.xml -o json dedup services
psc -c cfg.xml -o json dedup merge --keep h-web1 --remove web-primary   # dry-run plan
psc -c cfg.xml dedup merge --keep h-web1 --remove web-primary --apply --out fixed.xml
```

`merge` repoints **every** group/security-rule/NAT reference onto `--keep`
*before* deleting `--remove`. It **blocks** (exit `6`) on a value mismatch
(use `--allow-value-change` to override) or if the survivor isn't visible where
a reference lives. Per-object locations: `--keep-location` / `--remove-location`
(default: `--device-group` or `shared`).

### refs — where-used, unused, dangling

```bash
psc -c cfg.xml -o json refs used h-web1            # delete/rename pre-flight
psc -c cfg.xml -o json refs unused --kind address # recursive: nothing a rule reaches
psc -c cfg.xml -o json refs dangling              # references to missing objects
```

`refs used` may need `--kind` and `--location` if a name is ambiguous.

### name — opt-in naming templates

```bash
psc -c cfg.xml -o json name lint                  # drift vs the configured scheme
psc -c cfg.xml name rename --object h-web1 --to H-10.0.0.10   # reference-aware
psc -c cfg.xml name apply --object h-web1          # rename to the scheme's name
```

Rename **refuses** a shared-vs-device-group shadow collision (exit `6`).

### profile — live connections

```bash
psc init --name prod --host panorama.example.com --user admin   # password→API key, verify, save (0600)
psc login                                                       # verify the stored key (show system info)
psc login --user admin                                          # rotate the key (re-keygen + verify)
psc profile add --name prod --host panorama.example.com --api-key "$KEY" --default  # scriptable, key in hand
psc profile list
```

Password comes from `$PSC_PASSWORD` or a hidden prompt, never a flag. Auth
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

## What NOT to do

- DO NOT add `--apply` to a read command by reflex (ignored on reads, dangerous habit).
- DO NOT parse the `table` format — use `json`/`jsonl`.
- DO NOT apply a plan whose `blockers` is non-empty — fix the cause.
- DO NOT expect live `--apply` in v0.1 — use `-o set` or offline `--apply --out`.
- DO NOT put `--apply`/`--out` before the subcommand, or `-c`/`-o` after it.

## When something fails

1. Check the exit code (`echo $?`) and the envelope `type`.
2. For a blocked plan (exit 6), read `details.blockers` — it names the exact
   unsafe reference.
3. Re-run with `--debug` for structured logs on stderr.
