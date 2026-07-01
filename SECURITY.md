# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately via GitHub Security Advisories
(**Security → Report a vulnerability**) on this repository, rather than opening a
public issue. You'll get an acknowledgement and a fix timeline.

## Hardening posture

The v1.0.0 security review ([#78](https://github.com/thomaschristory/panorama-super-cli/issues/78))
found no High/Critical issues. The code-level findings are addressed:

- **Supply chain (M/L).** Every GitHub Actions `uses:` is pinned to a full
  40-character commit SHA (with the version in a trailing comment); Dependabot's
  `github-actions` updater keeps the SHAs current.
- **Least privilege (L).** `test.yml` and `lint.yml` declare
  `permissions: { contents: read }`; `docs.yml`/`release.yml` already scope
  their tokens explicitly.
- **Secret at rest (L).** `~/.psc/config.yaml` (which may hold an API key) is
  created `0600` atomically via `os.open(..., O_CREAT|O_TRUNC, 0o600)` — there is
  no world-readable window between create and `chmod` — and the parent `~/.psc`
  directory is `0700`. A pre-existing looser file is repaired on write.
- **Keep the key off disk.** Set `PSC_API_KEY` in the environment to override the
  profile's stored `api_key` (precedence: env > config file), so the secret need
  never be written to disk.
- **Transport (L).** TLS verification is on by default. When a profile runs with
  `--insecure` (`verify_ssl=false`), psc now emits a loud `InsecureTLSWarning`
  on every live connection — especially the password-bearing key-fetch — because
  credentials cross the wire MITM-able. Never use `--insecure` against
  production Panorama.

## Remaining hardening (repository settings — maintainer action)

These are configuration, not code, and must be enabled in the GitHub repo
**Settings → Code security**:

- **Enable Dependabot alerts** (finding #3): surfaces newly-disclosed CVEs
  against the locked dependency set between the weekly version-update PRs.

Informational findings #7 (floating `>=` lower bounds, mitigated by the
hash-pinned `uv.lock` + `uv sync --frozen`), #8 (advisory cross-check clean),
and #9 (strong branch protection) require no action.
