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

Configure a profile once:

```console
psc profile add --name prod --host panorama.example.com --api-key "$PANOS_KEY" --default
psc profile list
```

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

!!! warning "Live writes are v0.2"
    Live `--apply` is not yet implemented. Plan against the live config with
    `-o set`, or plan offline and `load config partial` the result. See
    [Writes and safety](safety.md).

## Choosing a source

| | Offline | Live |
| --- | --- | --- |
| Credentials | none | API key |
| Reads | ✅ | ✅ |
| Writes | new file via `--out` | v0.2 |
| Best for | audits, CI, safe edits | quick lookups against prod |
