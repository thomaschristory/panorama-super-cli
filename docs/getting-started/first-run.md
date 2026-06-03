# First run

This walkthrough uses an offline config export (`panorama.xml`). Every command
works identically against a live profile — see [Live vs offline](../guides/live-vs-offline.md).

## 1. Is this IP already an object?

```console
$ psc -c panorama.xml find ip 10.0.0.10
```

You'll see every matching object: **exact** matches, broader objects that
**contain** the IP (e.g. a `/24`), narrower objects **within** a queried range,
and the address-groups that carry them.

Pipe it to an agent or `jq` with JSON:

```console
$ psc -c panorama.xml -o json find ip 10.0.0.10 | jq '.exists'
true
```

## 2. Find duplicates

```console
$ psc -c panorama.xml dedup addresses
```

Each row is a set of objects that mean the same thing under different names.

## 3. Preview a merge (dry-run)

```console
$ psc -c panorama.xml dedup merge --keep h-web1 --remove web-primary
```

Nothing changes. You see exactly which groups and rules would be rewritten and
in what order. Want the PAN-OS commands instead?

```console
$ psc -c panorama.xml -o set dedup merge --keep h-web1 --remove web-primary
```

## 4. Apply it

Offline, write the cleaned config to a **new** file (never the source export):

```console
$ psc -c panorama.xml dedup merge --keep h-web1 --remove web-primary \
      --apply --out fixed.xml
```

Load `fixed.xml` into Panorama (`load config partial` or the GUI), review the
candidate, and commit.

## 5. Audit hygiene

```console
$ psc -c panorama.xml refs unused --kind address
$ psc -c panorama.xml refs dangling
$ psc -c panorama.xml name lint
```

That's the loop: **find → preview → apply → audit.** Read
[Writes and safety](../guides/safety.md) before you `--apply` against anything
you care about.
