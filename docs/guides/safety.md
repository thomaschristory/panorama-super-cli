# Writes and safety

`psc` is built so that the *default* behaviour is safe and the dangerous thing
is always explicit.

## Dry-run by default

Every mutating command (`dedup merge`, `name rename`, `name apply`) **prints a
plan and exits without changing anything** unless you pass `--apply`. The plan
you see is the exact change-set that `--apply` would execute — there's no
second, hidden code path.

```console
psc -c panorama.xml dedup merge --keep h-web1 --remove web-primary   # dry-run
psc -c panorama.xml dedup merge --keep h-web1 --remove web-primary --apply --out fixed.xml
```

## Repoint before delete

A merge or rename never deletes or renames an object until every reference to it
has been rewritten — across `shared` and every device-group, in groups, security
rules, and NAT. The change-set is ordered: upserts → reference rewrites →
renames → deletes.

## Blockers are a hard gate

A change-set carries a `blockers` list. If it's non-empty, the plan is **unsafe**
and `psc` refuses to apply it — even with `--apply` — exiting `6` (`conflict`).
Blockers are raised instead of doing something surprising. Examples:

- merging objects with different values (changes what rules match),
- a reference that can't be repointed because the survivor isn't visible there,
- a rename that would shadow a same-named object in another scope.

Warnings (e.g. a NAT translation field that needs manual review) are surfaced but
don't block.

## Offline apply never overwrites your export

Offline, `--apply` requires `--out PATH` and writes the rewritten config there —
it will refuse to write back over the source file. Your export stays pristine and
the change is reviewable as a diff.

```console
psc -c panorama.xml dedup merge --keep a --remove b --apply --out fixed.xml
diff <(xmllint --format panorama.xml) <(xmllint --format fixed.xml)
```

## Live writes

Live `--apply` is not implemented in v0.1. To change a live Panorama today:

1. Plan against the running config (`--profile prod ... -o set`), **or**
2. Plan offline and apply to a new file, then `load config partial` it.

Either way you review a candidate config and commit on Panorama yourself —
`psc` never commits for you.

## Debugging

`--debug` streams structured logs to **stderr**; stdout stays clean for pipes.
