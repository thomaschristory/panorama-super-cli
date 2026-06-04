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

Offline, `--out PATH` writes the rewritten config there — it will refuse to
write back over the source file. Your export stays pristine and the change is
reviewable as a diff. `--out` is an artifact request, so it is honoured even in
a dry-run (writing a file is not a mutation); `--apply` alone still requires
`--out`.

```console
psc -c panorama.xml dedup merge --keep a --remove b --apply --out fixed.xml
diff <(xmllint --format panorama.xml) <(xmllint --format fixed.xml)
```

By default the `--out` file is the rewritten config XML. Pass
`-of/--output-format set` to instead write the equivalent PAN-OS `set` script
(the creates/deletes/repoints that achieve the same change) — easier to read and
to paste into a config session. The blocker gate and repoint-before-delete
ordering apply to both formats identically; a blocked plan writes no file.

```console
psc -c panorama.xml dedup merge --keep a --remove b --apply --out plan.set -of set
```

## Live writes

Live `--apply` pushes the plan to Panorama's **candidate** config over the XML
API and **never commits** — `psc` leaves a candidate for you to review and
commit yourself, just as offline leaves a `--out` file.

```console
psc -p prod dedup merge --keep h-web1 --remove web-primary          # dry-run
psc -p prod dedup merge --keep h-web1 --remove web-primary --apply  # writes the candidate
```

The same safety contract holds on the wire: `blockers` refuse the apply before
any device contact, and references are repointed *before* the object is deleted.
A name carrying a single quote can't be addressed by an XML-API xpath, so it's
rejected up front (`input`, exit `2`) rather than sent malformed — rename it or
apply that plan offline. If a write fails mid-plan, `psc` reports how far it got
and leaves the uncommitted candidate for you to inspect or revert.

Prefer to stage the change as a file instead? Add `--out plan.set -of set` (it
writes the artifact without pushing, even on a live profile), or print the
script to stdout with `-o set` (paste / `load config partial`).

## Debugging

`--debug` streams structured logs to **stderr**; stdout stays clean for pipes.
