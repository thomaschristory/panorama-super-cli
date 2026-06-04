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

Profiles live in `~/.psc/config.yaml` (created `0600`, since it holds the API
key). A profile can set a default `device_group` scope. See
[Configuration](../reference/config.md).

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
