# Using with AI agents

`psc` is designed to be driven by AI agents and scripts, not just humans.

## The contract

1. **Always pass `-o json`.** Stdout becomes a stable JSON document; errors come
   back as a typed envelope on stdout. (Even without it, a non-TTY stdout
   auto-switches to JSON — but be explicit.)
2. **Branch on the exit code, then the `type`.** Exit codes are stable; see
   [Exit codes](../reference/exit-codes.md).
3. **Dry-run first, then `--apply`.** The dry-run plan *is* the change-set that
   `--apply` executes. Read it, decide, then apply. A blocked plan exits `6`
   with `blockers` in the envelope `details`.

```bash
# Is this IP already an object?
if psc -c cfg.xml --strict -o json find ip "$ip" >/tmp/r.json; then
  jq '.matches[].name' /tmp/r.json
else
  echo "exit $? — not an object yet"
fi
```

## Patterns

- **Resolve a list of IPs in one call:**
  ```bash
  psc -c cfg.xml -o json find ip -f ips.txt | jq '[.[] | {q:.query, exists}]'
  ```
- **Preview a merge as data, decide, then apply:**
  ```bash
  plan=$(psc -c cfg.xml -o json dedup merge --keep a --remove b)
  echo "$plan" | jq -e '.blockers | length == 0' >/dev/null \
    && psc -c cfg.xml dedup merge --keep a --remove b --apply --out fixed.xml
  ```
- **Get paste-ready PAN-OS commands** instead of mutating a file:
  ```bash
  psc -c cfg.xml -o set dedup merge --keep a --remove b
  ```
- **Gate CI on hygiene:**
  ```bash
  psc -c cfg.xml --strict refs dangling
  psc -c cfg.xml --strict name lint
  ```

## Bundled Skill

`psc` ships an [Agent Skill](https://github.com/thomaschristory/panorama-super-cli/blob/main/skills/panorama-super-cli/SKILL.md)
(installed alongside the package) describing the command surface, the safety
model, and the JSON/exit-code contract — so a capable agent can use `psc`
correctly from a cold start.

Drop it where your harness loads user-scoped skills with `psc skill install`
(dry-run by default — add `--apply` to write):

```bash
psc skill install --target claude-code --apply   # ~/.claude/skills/panorama-super-cli/
psc skill install --target codex --apply          # ~/.agents/skills/panorama-super-cli/
psc skill export ./vendor/skills --apply          # ./vendor/skills/panorama-super-cli/
```

Supported `--target` values: `claude-code`, `codex`, `gemini`, `copilot`. Re-run
after upgrading `psc` to refresh an installed copy.

## Don't

- Don't reflexively add `--apply` to a read command (it's ignored, but the habit
  bites on writes).
- Don't parse the `table` format — it's for humans. Use `json`/`jsonl`.
- Don't apply a plan whose `blockers` is non-empty; fix the cause instead.
