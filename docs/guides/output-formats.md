# Output formats

Pick a format with `-o/--output`. Valid values: `table`, `json`, `jsonl`,
`yaml`, `csv`, `set`.

```console
psc -c panorama.xml -o json find ip 10.0.0.10
```

## Defaults and auto-switching

- On an interactive terminal, the default is **`table`** (rich, human-friendly).
- When stdout is **not a TTY** (piped, captured, redirected), `psc`
  automatically switches to **`json`** — so agents and scripts get parseable
  output without having to pass `-o json` every time.

You can still force any format explicitly; an explicit `-o` always wins. The
config file's `defaults.output` sets the interactive default.

## The formats

| Format | Use it for |
| --- | --- |
| `table` | Reading at a terminal. |
| `json` | Agents, `jq`, anything that parses. Pretty-printed. |
| `jsonl` | Streaming one record per line. |
| `yaml` | Human-readable structured output. |
| `csv` | Spreadsheets, quick diffs. Flattened. |
| `set` | PAN-OS `set` commands for plan commands (paste-ready). |

Machine formats are never line-wrapped, so long JSON values stay valid.

## `set` output

For mutating commands (`dedup merge`, `name rename`/`apply`), `-o set` renders
the plan as PAN-OS CLI commands:

```console
$ psc -c panorama.xml -o set dedup merge --keep h-web1 --remove web-primary
# merge address 'web-primary'@shared -> 'h-web1'@shared
delete shared address-group grp-web static
set shared address-group grp-web static [ h-web1 ]
delete shared pre-rulebase nat rules nat-web source
set shared pre-rulebase nat rules nat-web source [ h-web1 ]
delete shared address web-primary
```

Member-list edits become `delete` + `set` because PAN-OS `set` on a member field
*appends* — the leading `delete` makes the result a true replace. Lines a tool
can't safely render (NAT translation paths) appear as `# REVIEW` comments; the
structured (`json`) plan still carries them.

### `-o set` (stdout) vs `--output-format set` (the `--out` file)

These are two different knobs and it's worth keeping them straight:

- **`-o set`** controls what a command prints to **stdout** — use it to read or
  pipe the plan during a dry-run.
- **`-of` / `--output-format`** (mutating commands only) controls the format of
  the **`--out` file artifact**: `xml` (default) rewrites the whole config to
  load with `load config`, while `set` writes the same PAN-OS `set` script shown
  above to that file — easier to read and to paste into a config session or
  `load config partial`.

```console
psc -c panorama.xml dedup merge --keep h-web1 --remove web-primary --out plan.set -of set
```

`-of` only shapes the `--out` file. `--out` writes that file whenever it's
given — including in a dry-run, and on a live profile — because writing a file
never touches the source export or the device candidate.

## Errors

Errors are emitted as a stable JSON envelope:

```json
{"error": "human message", "type": "conflict", "details": { }}
```

On `-o json` the envelope goes to **stdout** (so the same pipe gets it);
otherwise to **stderr**, keeping stdout clean. See [Exit codes](../reference/exit-codes.md).
