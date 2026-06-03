# Exit codes

`psc` exit codes are a stable scripting contract. Each maps to the `type` field
in the JSON error envelope.

| Code | `type` | Meaning |
| --- | --- | --- |
| `0` | — | Success. |
| `1` | `internal` | Unexpected error (a bug — please report). |
| `2` | — | Usage error from the CLI framework (bad flags/args). |
| `3` | `input` | Unreadable or invalid config / input file. |
| `4` | `validation` | Bad user input (invalid IP, malformed object spec). |
| `5` | `not_found` | A lookup found nothing (with `--strict`). |
| `6` | `conflict` | A plan was blocked/unsafe and refused. |
| `7` | `transport` | Live API connection failure. |
| `8` | `auth` | Live API authentication failure. |
| `9` | `config` | Profile/config problem (incl. no source given). |

## The error envelope

On failure, `psc` prints a JSON envelope. With `-o json` it goes to **stdout**
(same pipe as data); otherwise to **stderr**.

```json
{
  "error": "plan blocked (unsafe): value mismatch: keep=ip-netmask:10.0.0.0/24 ...",
  "type": "conflict",
  "details": { "blockers": ["..."], "warnings": [] }
}
```

`details` is present when there's structured context — most usefully the
`blockers` list for a refused plan (exit `6`) and candidate disambiguation for an
ambiguous `refs used` (exit `4`).

## Scripting

```bash
psc -c cfg.xml -o json dedup merge --keep a --remove b --apply --out fixed.xml
case $? in
  0) echo "merged" ;;
  6) echo "blocked — inspect .details.blockers" ;;
  *) echo "error $?" ;;
esac
```
