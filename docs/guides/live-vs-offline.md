# Live vs offline

`psc` reads from one of two **sources**. Every read command behaves identically
against either, because both produce the same snapshot.

## Offline (`--config file.xml`)

Point `psc` at an exported Panorama config:

```console
psc --config panorama.xml find ip 10.0.0.10
```

- No credentials, no network, totally read-only against your device.
- `--apply` writes the rewritten config to a separate `--out` file.
- Best for audits, CI, and trying things safely.

XML is parsed with `defusedxml`, so even an untrusted config file can't trigger
XXE / billion-laughs attacks.

## Live (`--profile name`)

Configure a profile once — `psc init` fetches the API key from your
username/password and verifies it before saving:

```console
psc init --name prod --host panorama.example.com --user admin   # prompts for the password
psc profile list
```

(Already hold a key? `psc profile add --name prod --host … --api-key "$PANOS_KEY" --default`.)

Then run any read command:

```console
psc --profile prod refs unused --kind address
psc -p prod find ip 10.0.0.10 -o json
```

`psc` fetches the running config over the PAN-OS XML API (via
`pan-os-python`) and builds the same snapshot the offline parser would.

Profiles live in `~/.psc/config.yaml` (created `0600` atomically, since it holds
the API key; the parent `~/.psc` is `0700`). A profile can set a default
`device_group` scope. See [Configuration](../reference/config.md).

### Keeping the key off disk

Set **`PSC_API_KEY`** in the environment to override a profile's stored `api_key`
(precedence: env > config file), so the secret need never be written to disk — a
good fit for CI secrets and short-lived shells:

```console
PSC_API_KEY="$PANOS_KEY" psc -p prod find ip 10.0.0.10 -o json
```

### TLS and `--insecure`

TLS certificates are verified by default. For a self-signed Panorama you can pass
`--insecure` to `psc init` (recorded as the profile's `verify_ssl: false` and
reused by later live commands) — but psc then emits a loud `InsecureTLSWarning`
on **every** live connection, because credentials cross the wire MITM-able.

!!! warning "Never `--insecure` against production"
    `--insecure` disables certificate verification, including on the
    password-bearing key-fetch during `init`. Use it only against a lab Panorama
    with a self-signed cert, never against production. `--no-verify` is unrelated:
    it skips the reachability *probe*, not certificate checking.

Live `--apply` pushes the plan to Panorama's **candidate** config over the XML
API and **never commits** — you review the candidate and commit yourself, the
device-side analog of reviewing the offline `--out` file. The same `blockers`
gate and repoint-before-delete ordering apply on the wire. See
[Writes and safety](safety.md).

## Choosing a source

| | Offline | Live |
| --- | --- | --- |
| Credentials | none | API key |
| Reads | ✅ | ✅ |
| Writes | new file via `--out` | candidate config (you commit) |
| Best for | audits, CI, safe edits | lookups and edits against prod |

`--out` works on either source — it saves a reviewable `set`/`xml` artifact
without touching the export or the candidate, even in a dry-run. On live it's
independent of `--apply`: use `--apply` to push the candidate, add `--out` if
you also want the script on disk.
