# Configuration

`psc` reads `~/.psc/config.yaml` (override the path with the `PSC_CONFIG`
environment variable). The offline path needs no config at all; this file exists
for live profiles and an opt-in naming scheme.

## Location

Resolved via `platformdirs`:

- Linux: `~/.config/psc/config.yaml`
- macOS: `~/Library/Application Support/psc/config.yaml`

The file is created `0600` because it can hold API keys.

## Schema

```yaml
default_profile: prod

profiles:
  - name: prod
    hostname: panorama.example.com
    api_key: "REDACTED"
    port: 443
    verify_ssl: true
    device_group: DG-EDGE   # optional default scope for this profile

defaults:
  output: table             # table | json | jsonl | yaml | csv | set
  naming:
    host: "H-{ip}"
    network: "N-{network}_{prefix}"
    range: "R-{start}-{end}"
    fqdn: "FQDN-{fqdn}"
    wildcard: "W-{value}"
    service_tcp: "tcp-{port}"
    service_udp: "udp-{port}"
    lowercase: false
```

## Managing profiles

Use the CLI rather than hand-editing:

```console
psc profile add --name prod --host panorama.example.com --api-key "$PANOS_KEY" --default
psc profile list
psc profile remove prod
```

`-p/--profile` overrides the default for a single invocation; if no profile is
named and there's no `default_profile`, live commands error with `config`
(exit `9`).

## Naming scheme

The `defaults.naming` block drives [naming templates](../guides/naming.md).
Override any subset of the format strings; placeholders available per kind are
shown above. Set `lowercase: true` to force generated names to lower-case. All
generated names are sanitized to PAN-OS rules (≤63 chars, leading alphanumeric).
