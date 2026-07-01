# Open issue bodies (snapshot)


## #3
### find ip --resolve-fqdn: match FQDN objects whose DNS resolves to the target IP

labels: area:find

Today `find ip` matches IP objects numerically and FQDN objects by exact FQDN. Add opt-in `--resolve-fqdn` to DNS-resolve FQDN objects and match those whose resolved A/AAAA includes the queried IP.

Acceptance: `find ip 1.2.3.4 --resolve-fqdn` surfaces FQDN objects resolving to 1.2.3.4; offline default unchanged; resolver is cached + timeout-bounded.


## #4
### dedup merge --group: collapse an entire duplicate set in one safe plan

labels: area:dedup, safety

Extend merge to accept a whole duplicate bucket: pick one survivor, drop the rest, one change-set repointing all references. Builds on `dedup addresses`.

Acceptance: `dedup merge --group <value> --keep <name>` produces one plan; all same blockers apply; tested.


## #9
### Audit: objects referenced only by disabled rules

labels: area:refs

Surface objects whose only references are in disabled rules — candidates for cleanup once the rules are removed. Reachability variant that ignores disabled rules as roots.

Acceptance: `psc refs unused --ignore-disabled` or `psc audit only-disabled`; tested against a disabled-rule fixture.


## #11
### Audit: services duplicating predefined/well-known ports

labels: area:refs

Flag custom service objects that duplicate predefined services (service-http/https) or IANA well-known ports, so they can be consolidated onto the predefined names.

Acceptance: `psc audit services-vs-wellknown` lists offenders + the predefined they shadow.


## #13
### psc diff: drift between two configs or two device-groups

labels: area:output

Compare two snapshots (two files, or two device-groups in one config) and report object/group/rule differences — useful for pre/post-change review and DG drift.

Acceptance: `psc diff a.xml b.xml` and `psc diff --device-group A --against B`; structured + human output.


## #14
### Bulk import/export of objects (NDJSON)

labels: area:core

Export objects to NDJSON and bulk create/update from NDJSON with the same `--apply` rule and per-line validation, so large changes are one operation rather than N.

Acceptance: `psc export addresses` / `psc set address -f objs.ndjson --apply`; malformed line -> input error; connection/file reuse.


## #15
### name apply --all: bulk reference-aware rename to the scheme

labels: area:naming, safety

Rename every non-compliant object to its scheme name in one reviewed plan, skipping/blocking any that would collide or shadow. Builds on `name lint` + `plan_rename`.

Acceptance: one change-set for all renames; per-object blockers surfaced; dry-run default; tested for collision handling.


## #26
### Audit: unused tags over-counts shadowed/cross-scope tags in dynamic-filter matching

labels: area:refs

`ReferenceGraph._unused_tags` matches a dynamic address-group filter's quoted tag tokens (e.g. `'prod' and 'web'`) against **every** same-named tag in the snapshot, regardless of which location/scope the filter can actually see:

```python
for ag in self.snapshot.address_groups:
    if ag.dynamic_filter:
        filter_tags = set(re.findall(r"'([^']+)'", ag.dynamic_filter))
        for t in self.snapshot.tags:
            if t.name in filter_tags:
                used.add((t.location.name, t.name))
```

So a filter in `DG-A` referencing `'prod'` marks `prod`@shared, `prod`@DG-B, and every other `prod` as "used" — including tags not visible from the filter's location, and shadowed copies the filter can't resolve to. Result: genuinely-unused tags can be reported as used (false negative in `refs unused --kind tag`).

This is pre-existing (predates the nested-DG work in #12) and was already imprecise in the flat model; nested device-groups make the shadowing case more visible.

**Fix:** resolve each filter's tag tokens through `Snapshot.ancestors(ag.location)` (closest definition wins), marking only the tag the filter actually binds to, mirroring how object references already resolve. Add fixtures covering a shadowed tag and a sibling-DG same-named tag.

Acceptance: a tag only reachable via a filter in a scope that can't see it is reported unused; the shadowing/visible copy is reported used.


## #56
### Audit/unused: surface the scan-scope blind spot (templates, network/device config, etc.)

labels: area:refs, area:output, safety

## Problem

`refs unused` means "unreferenced by the **device-group objects and policy rulebases** in the parsed config" — but it reads as "safe to delete." psc never parses several legitimate reference sites, so an in-use object can be reported `unused`, and deleting it breaks something with no warning.

**Not scanned (danger-ordered):**
1. **Templates / template-stacks and all network/device config** — not parsed at all. Address objects used by IKE/IPSec gateways, GlobalProtect portals/gateways, service routes, DNS proxy, static routes, interface addresses, log-forwarding/SNMP/syslog destinations → reported unused.
2. **NAT-rule tags** (separate issue).
3. **Dynamic address group membership** — DAGs match by tag expression; psc resolves no DAG membership, so an address used only via a DAG can look unused.
4. **Object categories psc doesn't model** — profiles & profile-groups, schedules, EDLs, applications/app-groups, custom URL categories, regions, HIP profiles, user-groups (also cause `dangling` false-positives when their names appear in scanned rule fields).
5. **Single-snapshot scope** — references from another Panorama, pushed templates, or firewall-local config are invisible.

Contrast: `merge`/`rename` are protected (they repoint scanned sites and **block** when they can't). **Deletion driven by `unused` is the unprotected operation** — exactly where an unseen reference becomes an outage.

## Asks

- **Docs (done):** dedicated "Coverage and blind spots" guide + point-of-use warnings on `unused`, safety, concepts, and the agent SKILL. (PR linked below.)
- **UX (near-term):** print a one-line caveat on `refs unused` output (stderr, so machine rows stay clean) — e.g. "candidates only; not scanned: templates/network config, NAT tags, DAG membership — verify before deleting." Possibly a `--quiet`/config opt-out.
- **Coverage (longer-term, separate issues):** optionally parse template/network references; resolve DAG membership for reachability; model more object kinds.

This issue tracks the umbrella + the near-term UX caveat. The deeper scanning work should be split into focused issues.


## #76
### move: auto-cascade dependencies (and bulk selection) when promoting toward shared

labels: enhancement, area:core, safety

Follow-up to #74 / `psc move` (shipped in v0.4.2). v1 deliberately kept two
limitations; this tracks lifting the first (and notes the second).

## Limitation today

`psc move` promotes a **single** object toward `shared`, and **refuses** (blocker,
exit 6) when the object's own downward dependencies aren't already visible at the
destination — the operator must move each dependency first, in the right order:

- an address carrying a DG-local **tag** → blocked until the tag is promoted;
- a static **address-group** whose members are DG-local → blocked until every
  member is promoted;
- a **service-group** with DG-local members; a dynamic group whose **filter
  tags** are DG-local.

For a deep group this is a tedious manual leaf-to-root walk — exactly the kind of
reference-safe cascade `psc` exists to automate (cf. `decommission`'s fixpoint
teardown).

## Proposed fix: opt-in `--cascade`

Add a `--cascade` flag to `psc move` that pulls the transitive dependency closure
up to the destination too, in dependency order (members/tags before the objects
that reference them), as **one inspectable `ChangeSet`**. Without the flag, keep
today's block-and-list behaviour (smallest blast radius stays the default).

Design notes / open questions:
- **Ordering & fixpoint.** Reuse the closure logic from `dedup.resolve_group_members`
  / `_group_closure` and the ordered-plan discipline from `core/decommission.py`.
  Emit upserts for the deepest dependencies first, then their parents, then the
  named object, then the source deletes — all in one plan.
- **Per-dependency safety still applies.** Each cascaded object must independently
  pass the move gates: direction (already guaranteed — all go to the same
  destination), the **intermediate-shadow guard**, and **collision** handling
  (identical-value dep already at dest → just drop the source copy; different
  value → blocker for the whole cascade).
- **Shared dependencies.** A member referenced by *other* objects that remain in
  the source DG must not be removed from the source — promoting it to shared is
  still safe (it stays visible to the source via inheritance), but the source
  copy can only be deleted once nothing in the source subtree needs the local
  definition. Likely: promote (create-at-dest) the dependency but **only delete
  its source copy when it has no remaining local referrers** — needs a
  where-used check per cascaded object, and a clear warning when a source copy is
  intentionally left behind.
- **Cycle safety** for nested groups (the closure walk is already cycle-safe in
  `dedup`).

## Related (second v1 limitation — maybe its own issue)

`move` is single-object per run. A cleanup-oriented **bulk/filtered** mode (e.g.
`move --all-in DG-EDGE --kind address --to shared`, or feeding `refs unused` /
`find` output) would pair naturally with `--cascade`. Splitting this out unless
it's cheap to fold in.

## Acceptance

- `psc move <group> … --to shared --cascade` promotes the whole dependency
  closure in one ordered, dry-run-able plan; offline `--apply` round-trips.
- A cascaded dependency still referenced locally in the source is promoted but
  its source copy is retained, with a warning.
- All the existing single-object blockers still fire per cascaded object.
- Without `--cascade`, behaviour is unchanged (block + list deps to move first).


## #78
### Security Audit Report: 1 medium, 5 low, 3 info findings (supply-chain, CI/provenance, SAST)

labels: 

## Security Audit Report

**Repository:** `thomaschristory/panorama-super-cli`
**Audit date:** 2026-06-11
**Scope reference:** default branch `main` at HEAD

### Scope & Methodology

A full security audit was performed against the repository at HEAD on `main`, covering:

- **Supply-chain & dependency review** — direct/transitive dependency constraints (`pyproject.toml`), the hash-pinned lockfile (`uv.lock`), and a CVE cross-check of all notable resolved versions against the GitHub Advisory Database (PIP ecosystem).
- **CI/CD & provenance review** — all GitHub Actions workflows (`test.yml`, `lint.yml`, `docs.yml`, `release.yml`), action-ref pinning, `GITHUB_TOKEN` permission scoping, trigger safety, PyPI trusted-publishing flow, and repository branch-protection settings via the GitHub API.
- **Static analysis (SAST)** — pattern-based source review of the Python package (`psc/`), focused on credential handling and transport security.
- **Secret scan** — HEAD-only scan for committed credentials.

**Limitations:**
- SAST was **pattern-based**; there is no full inter-procedural dataflow/taint engine behind these results, so subtle data-flow vulnerabilities may not be detected.
- The secret scan covered the **current HEAD only**, not the full git history; secrets that were committed and later removed would not be surfaced.
- During verification, **2 candidate findings were investigated and dismissed as false positives**, and are not included below.

All findings below were adversarially verified against HEAD before inclusion. Severities reflect the post-verification adjusted severity.

### Summary of Findings

| Severity | Count |
|----------|-------|
| Critical | 0 |
| High     | 0 |
| Medium   | 1 |
| Low      | 5 |
| Info     | 3 |
| **Total**| **9** |

---

### Findings

#### 1. PyPI-publishing action pinned to a mutable branch ref in an OIDC-privileged job

- **Severity:** Medium
- **Category:** Supply-chain / mutable action reference
- **Location:** `.github/workflows/release.yml` — "Publish to PyPI (trusted publishing)" step

**Evidence:**
```yaml
permissions:
  contents: write
  id-token: write
...
      - name: Publish to PyPI (trusted publishing)
        uses: pypa/gh-action-pypi-publish@release/v1
```

**Impact:** The release workflow runs on `v*` tags with `contents: write` and `id-token: write`, publishing to PyPI via OIDC trusted publishing. `release/v1` is a mutable *branch* ref (more mutable than a tag). If that upstream branch were ever re-pointed or compromised, attacker-controlled code would execute in a job holding an OIDC token authorized to publish releases to PyPI under this project's name, plus a token able to write repo contents and create releases — a direct path to shipping a malicious release or exfiltrating the OIDC token.

This is rated **medium** (down from high): `release/v1` is the official, PyPA-maintained, documented consumption path for this action, so exploitation requires compromise of a reputable upstream org. The OIDC token is short-lived and scoped via a trusted-publisher binding to the workflow filename + tag pattern, and the workflow already has strong guardrails (tag-must-be-on-main check, tag-vs-package-version check, no PR/branch triggers). This is defense-in-depth hardening rather than an exploitable flaw in the project itself.

**Remediation:** Pin every `uses:` to a full 40-character commit SHA with the version in a trailing comment, e.g. `uses: pypa/gh-action-pypi-publish@<sha>  # release/v1`. This is the single highest-value hardening change given the release job's privileges. Dependabot's `github-actions` updater (already configured) will keep the SHA current.

---

#### 2. All third-party actions pinned to mutable version tags instead of commit SHAs

- **Severity:** Low
- **Category:** Supply-chain / mutable action reference
- **Location:** `.github/workflows/test.yml`, `lint.yml`, `docs.yml`, `release.yml` — `uses:` steps

**Evidence:**
```yaml
- uses: actions/checkout@v6
- uses: astral-sh/setup-uv@v7
- uses: actions/upload-pages-artifact@v5
- uses: actions/deploy-pages@v5
```

**Impact:** Every external action is pinned to a floating major-version tag rather than a full commit SHA. Version tags are mutable refs — the publisher (or an attacker who compromises the upstream action repo) can re-point `v6`/`v7`/`v5` to arbitrary code at any time, so the workflows lack reproducible provenance. `astral-sh/setup-uv` is notable because it runs in every job, including the privileged release job (`id-token: write` + `contents: write`).

Rated **low** (down from medium): all referenced actions are reputable, actively-maintained sources (`actions/*` is GitHub first-party; `astral-sh/setup-uv` is the official Astral action), there is no compromised action or concrete attack path present today, and this is a provenance/defense-in-depth posture issue.

**Remediation:** Pin each third-party action to a full-length commit SHA with the tag noted in a trailing comment (e.g. `actions/checkout@<sha>  # v6`). The same standard applies to first-party `actions/*` for full provenance. The already-configured Dependabot `github-actions` updater will maintain the SHAs.

---

#### 3. Dependabot security alerts disabled on the repository

- **Severity:** Low
- **Category:** Vulnerability monitoring / configuration
- **Location:** Repository settings (Dependabot alerts); `.github/dependabot.yml`

**Evidence:**
```
GET /repos/thomaschristory/panorama-super-cli/dependabot/alerts
  -> HTTP 403 {"message":"Dependabot alerts are disabled for this repository."}
security_and_analysis.dependabot_security_updates.status = "disabled"
```

**Impact:** `.github/dependabot.yml` configures weekly *version updates* for pip and github-actions (good), but the Dependabot security-alert / vulnerability-scanning feature is disabled at the repo level (confirmed two independent ways). Scheduled version-update PRs run on a fixed weekly cadence regardless of CVE disclosures; security alerts proactively surface known vulns in currently-pinned dependencies. With alerts off, a newly disclosed CVE against a version pinned in `uv.lock` may go unnoticed between scheduled bumps. This is a missing detective control (hardening gap), not an exploitable vulnerability, and is partially mitigated by the existing weekly version updates and enabled secret scanning / push protection.

**Remediation:** Enable Dependabot alerts (and ideally Dependabot security updates) in repo Settings > Code security, so advisories against the locked dependency set are flagged and patch PRs are opened automatically.

---

#### 4. CI workflows omit an explicit `permissions` block

- **Severity:** Low
- **Category:** Least-privilege / `GITHUB_TOKEN` scope
- **Location:** `.github/workflows/test.yml`, `.github/workflows/lint.yml` — top of file (no `permissions:` key)

**Evidence:**
```yaml
# test.yml and lint.yml: on:/jobs: present, no permissions: key
# compare docs.yml:
permissions:
  contents: read
  pages: write
  id-token: write
```

**Impact:** Neither `test.yml` nor `lint.yml` declares a top-level `permissions:` block, so the effective `GITHUB_TOKEN` scope falls back to the repository/organization default, which can be read-write if it has not been tightened. Both workflows only checkout, install dependencies, and run pytest/ruff/mypy — they need only `contents: read`. The stakes are elevated here because the repo ships a PyPI trusted-publishing pipeline, so a compromised action/dependency executing under an unexpectedly write-scoped token in CI is a more impactful pivot. This is defense-in-depth, conditional on the repo/org default being read-write.

**Remediation:** Add `permissions: { contents: read }` at the top of `test.yml` and `lint.yml` to make least-privilege explicit and independent of the repo-level default token setting. (`docs.yml` and `release.yml` already declare explicit scoped permissions.)

---

#### 5. API key persisted in plaintext with a write-before-chmod window

- **Severity:** Low
- **Category:** Sensitive data exposure / insecure storage
- **Location:** `psc/config/loader.py:44-52` (`save_config()`)

**Evidence:**
```python
buf = io.StringIO()
yaml.dump(config.model_dump(mode="json"), buf)
path.write_text(buf.getvalue(), encoding="utf-8")
path.chmod(0o600)  # contains API keys
```

**Impact:** `save_config()` writes the full config — including `Profile.api_key`, which grants full Panorama XML-API access — to `~/.psc/config.yaml` in cleartext, then applies `chmod 0600`. Because the `chmod` is applied *after* `write_text()`, the file is first created with the process umask (commonly 0644/0664). On a shared host, another local user can read the API key during the (microsecond) write-before-chmod window, and the key remains stored unencrypted at rest thereafter. `psc/config/models.py` documents that the key is stored plaintext ("treat the config file as a secret (it is created 0600)") and defaults `api_key` to an empty string, confirming this is an accepted, documented v0.1 design tradeoff. The race requires a co-located attacker actively racing the write, hence **low**.

**Remediation:** Create the file with restrictive permissions atomically before writing secrets — e.g. `os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)` (or set `os.umask` before the write) — and consider `chmod 0700` on the parent `~/.psc` directory. Longer term, support sourcing the key from an OS keyring or an environment variable (e.g. `PSC_API_KEY`) so it need not be written to disk at all.

---

#### 6. `--insecure` / `verify_ssl=False` disables certificate and hostname verification for live Panorama traffic

- **Severity:** Low
- **Category:** Insecure transport / TLS verification
- **Location:** `psc/core/source.py:65-77` (`_ssl_context`)

**Evidence:**
```python
ctx = ssl.create_default_context()
if not verify:
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
return ctx
```

**Impact:** When verification is disabled, this context is installed on the live device for key generation (`fetch_api_key`), the pre-flight probe (`verify`), config reads (`raw_xml`), and candidate pushes (`apply`). A profile created with `--insecure` (`auth_cmds.init` / `profile add`) makes the username+password keygen exchange and all subsequent API-key traffic trivially MITM-able, exposing credentials and allowing config tampering. This is **opt-in** (default `verify=True`) and is actually stricter than `pan-os-python`'s default (which exposes no SSL knob and never verifies TLS) — a deliberate lab-friendly escape hatch rather than an insecure default, hence **low**. The one substantive gap: when verification is off there is **no loud warning**, including during the password-bearing keygen.

**Remediation:** Keep the default-secure behavior, but warn loudly on stderr whenever a connection runs with verification disabled (especially during `fetch_api_key`, where a password crosses the wire), and document that `--insecure` must never be used against production Panorama. Optionally support pinning a per-profile CA bundle path so self-signed labs can verify without fully disabling checks.

---

#### 7. All direct dependencies declared with floating lower-bound (`>=`) constraints

- **Severity:** Info
- **Category:** Version pinning
- **Location:** `pyproject.toml` — `[project].dependencies` and `[dependency-groups]`

**Evidence:**
```
typer>=0.12, click>=8.1, rich>=13.7, pydantic>=2.7, ruamel.yaml>=0.18,
structlog>=24.1, platformdirs>=4.2, pan-os-python>=1.12, defusedxml>=0.7,
packaging>=23.0  (plus dev/docs groups: pytest>=8.2, ruff>=0.15, pyyaml>=6.0, ...)
```

**Impact:** Every runtime and dev/docs dependency uses an open-ended `>=` lower bound with no upper bound. In isolation, a fresh resolve could pull a future, untested, or compromised major version. This is substantially mitigated: `uv.lock` is committed and fully hash-pinned (566 sha256 entries), and all four CI workflows run `uv sync --frozen`, so CI and reproducible installs resolve only the locked, hash-verified set. Unbounded `>=` lower bounds are also standard, recommended practice for Python libraries (speculative upper caps cause resolver conflicts). The residual concern is purely theoretical and applies only to a downstream consumer who installs the published wheel without the lock — hence **info**.

**Remediation:** No action required. Continue relying on `uv sync --frozen` in CI (already done). Optionally add conservative upper bounds for security-sensitive libraries, and ensure published artifacts / CI never resolve outside the locked, hash-verified set.

---

#### 8. No known-vulnerable resolved dependency versions (advisory cross-check clean)

- **Severity:** Info
- **Category:** Vulnerability scan (positive result)
- **Location:** `uv.lock`

**Evidence:** GitHub Advisory DB (PIP) cross-check of resolved versions — all at or above the highest first-patched version:
```
requests 2.34.2 (>= <2.33.0 fix)        jinja2 3.1.6 (fixes GHSA-cpwx-vrp4-4pq7, <=3.1.5)
urllib3 2.7.0 (fixes GHSA-mf9v-mfxr-j63j, GHSA-qccp-gfcp-xxvc)
idna 3.18 (>3.15)                       certifi 2026.5.20 (>2024.7.4)
pyyaml 6.0.3 (>5.4)                     pydantic 2.13.4 (>=2.0.0,<2.4.0 fix)
virtualenv 21.4.2 (>20.36.1)            mkdocs 1.6.1 (only advisory ==1.2.2)
```

**Impact:** None — all notable resolved versions in `uv.lock` were cross-referenced against the GitHub Advisory Database and none fall within a known-vulnerable range. This documents a clean supply-chain state.

**Remediation:** No action required for current pins. Re-run the advisory cross-check whenever the lockfile is regenerated.

---

#### 9. No dangerous workflow patterns; strong branch protection (positive)

- **Severity:** Info
- **Category:** Positive finding
- **Location:** `.github/workflows/*`; `main` branch protection

**Evidence:**
```
branch protection: enforce_admins.enabled=true, required_linear_history.enabled=true,
  allow_force_pushes.enabled=false, allow_deletions.enabled=false,
  required_status_checks.strict=true contexts=[lint, test (ubuntu-latest, 3.12)]
release.yml: on: push tags v[0-9]*.[0-9]*.[0-9]*
```

**Impact (positive controls confirmed):**
- No `pull_request_target` triggers anywhere — untrusted PR code is never checked out into a secret-bearing context.
- No untrusted `${{ github.event.* }}` values interpolated into `run:` blocks; `release.yml` passes git context via `$GITHUB_REF_NAME` / `$GITHUB_SHA` shell env and validates tag-on-main (`git merge-base --is-ancestor`) and tag == package version before publishing.
- No `write-all` permissions; `release.yml`'s `contents: write` + `id-token: write` are justified and scoped, with a deliberate no-environment design to preserve the OIDC subject-claim binding.
- `release.yml` triggers only on `v*` tags (never PR/branch push).
- `secrets.GITHUB_TOKEN` is only passed to the first-party `gh` CLI, not to third-party actions.
- Branch protection on `main` is robust: admin enforcement, required linear history, force-push and deletion blocked, and strict required status checks (`lint`, `test`).

**Remediation:** No action required — recorded as positive controls. Addressing the SHA-pinning findings (#1, #2) above would bring the CI to a near-exemplary hardening posture.

---

*Report generated by automated security audit with adversarial verification. Severities reflect post-verification adjusted values.*



## #83
### workbench: edit the config/profile from within the TUI

labels: enhancement, area:workbench

Testing the workbench surfaced that there is no way to edit the config/profile file from inside the TUI. Today config is loaded at startup (`--config <export.xml>` or a profile from `~/.psc/config.yaml`) and any change requires dropping to the shell and running `psc profile ...` or hand-editing the YAML.

**Ask:** add an in-TUI config editor (view/add/update/remove profiles and defaults) so the user can point the workbench at a different export/profile without leaving it.

Relevant: `psc/cli/workbench_cmds.py`, `psc/config/loader.py`.


## #84
### workbench: show the object value in find/search results

labels: enhancement, area:find, area:workbench

The search/find results table shows only `kind`, `name`, `location` (`psc/tui/app.py`, results table). Because search is non-exclusive/fuzzy, results with very different values look interchangeable: e.g. a row for 10.0.0.0 and a row for 10.1.1.1 are indistinguishable because the actual object value is never printed.

**Ask:** add a `value` column (ip-netmask / ip-range / fqdn / service port, etc.) to the find results so the user can tell matched objects apart at a glance.

Relevant: `psc/tui/app.py` (search results rendering).


## #85
### workbench: dedup should let the user choose keep/drop and pick the device-group

labels: enhancement, area:dedup, area:workbench

When deduping, the workbench auto-decides which of the duplicates is kept and which is removed (`psc/tui/screens/dedup.py`: `keep, drop = group[0], group[1]`). Problems:

- The whole point of multi-selecting entries is to let the user decide on the dedup screen which one survives. Currently the choice is made for them.
- Behavior with 3+ selected duplicates is unclear/undefined: it appears to take the first two of the group and ignore the rest.
- The device-group is implied/fixed. It should be a drop-down so the user can change the target scope.

**Ask:**
1. On the dedup screen let the user explicitly pick the keep target (and therefore which get repointed + removed).
2. Define and support 3+ duplicates in one plan (collapse the whole set toward the chosen survivor). Related: #4.
3. Add a device-group drop-down instead of an implied DG.

Relevant: `psc/tui/screens/dedup.py`, `psc/core/dedup.py`.


## #86
### workbench: where-used should show usage for ALL selected objects, with an owning-object column

labels: bug, area:refs, area:workbench

On the usage / where-used screen, selecting multiple objects appears to show usage for only one of them. When several objects are selected, every selected object usage should be listed, and each usage row must indicate WHICH selected object it belongs to (an owning-object column: kind/name/location).

Otherwise it is impossible to tell whose reference a given row is.

**Ask:** render where-used for all selected objects and add/verify a column identifying the owning object per row.

Relevant: `psc/tui/screens/usage.py` (confirm the selection loop actually iterates the full selection and that the object identity column is populated).


## #87
### workbench: move should offer a destination drop-down, not hardcode shared

labels: enhancement, area:workbench

The move action in the workbench is hardcoded to move to `shared` (`psc/tui/screens/move.py`: `move <item> -> shared`, binding labelled "move to shared"). Moving to shared is a reasonable default, but the user should be able to choose the destination.

**Ask:** add a destination drop-down (shared or any device-group) on the move screen, defaulting to shared.

Related: #76 (auto-cascade dependencies when promoting toward shared).

Relevant: `psc/tui/screens/move.py`.


## #88
### workbench: inspect the staged changelist and drop individual staged changes

labels: enhancement, safety, area:workbench

The workbench only shows a `staged (N)` counter (`psc/tui/app.py`); there is no way to see what is actually staged. Staging is append-only and apply is all-or-nothing (`psc/tui/session.py`).

**Ask:** add a staged-changes screen that lists each staged change (with its plan summary), lets the user open/inspect an individual change, and drop a single staged change without discarding the whole batch.

Relevant: `psc/tui/session.py` (`staging: list[StagedChange]`, `apply_batch`), `psc/tui/app.py`.


## #89
### workbench: rename should ask which selected entry to rename, not auto-pick the first

labels: enhancement, area:naming, area:workbench

Rename auto-selects the first of the selected entries (`psc/tui/screens/rename.py`: `first_renameable()` returns `session.selection[0]`). When several entries are selected the user is not asked which one they want to rename.

**Ask:** when more than one entry is selected, prompt the user to choose which object to rename (or restrict rename to a single-selection action).

Relevant: `psc/tui/screens/rename.py`.


## #90
### docs: document the workbench TUI (docs site, README, CHANGELOG)

labels: documentation, area:workbench

The workbench TUI is not documented for users:

- **README.md**: no mention of the workbench / `psc workbench` (`psc w`).
- **docs site**: no dedicated guide page and no `mkdocs.yml` nav entry (only stray references under `docs/reference/`).
- **CHANGELOG.md**: has a line referencing #80, but the feature is otherwise undocumented.

**Ask:** add a workbench guide to the docs site (with nav entry), a section in the README, and a proper CHANGELOG entry for the release that ships it.

Relevant: `README.md`, `mkdocs.yml`, `docs/`, `CHANGELOG.md`.


## #91
### workbench: remove a single item directly from the selection list

labels: enhancement, area:workbench

Selection is built by toggling rows in the search results (space -> `session.toggle`). The hub shows a selection table (`psc/tui/app.py`, `_refresh_selection_view`), but there is no way to remove one item from the selection list itself. To deselect you must return to the exact search-result row and toggle it again, which is impractical once the search query changed or the row scrolled away. `clear_selection()` only wipes everything.

**Ask:** let the user remove the focused row directly from the selection panel (e.g. focus the selection table and press delete/backspace/space) to drop just that one item.

Relevant: `psc/tui/session.py` (`selection`, `toggle`, `clear_selection`), `psc/tui/app.py` (selection DataTable + `_refresh_selection_view`).


## #92
### offline apply: emit a partial config, not the whole rewritten Panorama config

labels: enhancement, area:output, area:workbench

Offline apply writes back the ENTIRE Panorama config. `apply_xml.apply_changeset()` mutates the parsed config in place and re-serializes the whole document; the workbench `OFFLINE_APPLY` path then writes that full `working_xml` (`psc/tui/session.py`), and the CLI `--output-format xml` does the same via `OfflineSource` (`psc/core/source.py`).

For review and for importing just the delta, a full config dump is heavy and hard to diff. It would be much more useful to emit a **partial config** containing only the changed subtree(s) (the touched device-group/objects/rules), suitable for targeted import.

**Ask:** add an output option that produces a minimal partial config XML (only the changed nodes) instead of the whole rewritten config.

Relevant: `psc/core/apply_xml.py`, `psc/core/source.py` (`_render_artifact`), `psc/tui/session.py` (`OFFLINE_APPLY`).


## #93
### workbench: SET mode should be able to write the set script to a file

labels: enhancement, area:output, area:workbench

In the workbench, SET output mode is preview-only: `apply_batch` renders `combined_set_script()` and returns it as `detail` with `out_path=None` — it never writes the script anywhere (`psc/tui/session.py`). Only `OFFLINE_APPLY` writes a file (and that file is the full XML config).

The CLI already supports writing a set script to a file (`--output-format set` -> `OfflineSource` -> `setcmd.render_changeset`), so the engine exists; it just is not wired into the workbench.

**Ask:** let the workbench write the combined set-command script to a file (a written output in SET mode), reusing the existing `render_changeset` path.

Relevant: `psc/tui/session.py` (`apply_batch`, `combined_set_script`, `OutputMode.SET`), `psc/core/setcmd.py` (`render_changeset`).


## #94
### workbench: object creation (parity with psc set)

labels: enhancement, area:core, area:workbench

The workbench can only operate on existing objects (search/select). There is no way to CREATE an object from the TUI — creation is only available via the CLI `psc set ...`. We need object creation in the workbench, matching the CLI.

**CLI reference (`psc set <kind>`, engine `psc/core/crud.py`, wired in `psc/cli/set_cmds.py`):**

- `set address` — `--name`, `--type` (ip-netmask|ip-range|ip-wildcard|fqdn), `--value`, `--description`, `--tag*`, `--location`
- `set address-group` — `--name`, `--member*` XOR `--filter`, `--description`, `--tag*`, `--location`
- `set service` — `--name`, `--protocol` (tcp|udp), `--dest-port`, `--source-port`, `--description`, `--tag*`, `--location`
- `set service-group` — `--name`, `--member*` (>=1), `--tag*`, `--location`
- `set tag` — `--name`, `--color` (color1..color42), `--comments`, `--location`

**Requirements:**
- A create screen per kind (or one screen with a kind picker) collecting the fields above, with a `--location` drop-down (shared or device-group) like the move destination picker (#87).
- Reuse the existing engine: build the `ObjectUpsert`/`ChangeSet` via `crud.plan_address/plan_address_group/plan_service/plan_service_group/plan_tag`. Do NOT reimplement validation.
- Surface `crud` validation inline: name rules, value-kind/port/color rules, and the cross-kind namespace collision + type/mode/protocol-change blockers (`ChangeSet.blockers` must gate staging, same as everywhere else).
- The created change goes through the normal staging flow (stage -> inspect -> apply), consistent with the staged-changelist work (#88).

Relevant: `psc/core/crud.py`, `psc/cli/set_cmds.py`, `psc/core/changeset.py` (`ObjectUpsert`), `psc/tui/session.py` (staging), `psc/tui/app.py` (hub + new screen binding).


## #95
### workbench: full feature parity with the CLI (one-to-one)

labels: enhancement, area:workbench

Tracking issue. The workbench should reach one-to-one feature parity with the CLI, exposing every CLI capability that makes sense interactively. Same engines (`psc/core`), same safety model (dry-run/stage, `ChangeSet.blockers` gate) — only the front-end differs. A CLI command is exempt only where it genuinely does not make sense in a TUI (noted below).

## CLI -> workbench parity matrix

| CLI command | Engine | Workbench today | Gap |
|---|---|---|---|
| `find` (ip/value/name) | resolve | search box | shows no object value -> #84 |
| `dedup` | dedup | `d` dedup | keep/drop choice, 3+, DG picker -> #85 |
| `refs used` (where-used) | refs | `u` usage | all-selected + owner column -> #86 |
| `refs unused` | refs | **missing** | add an unused-objects screen |
| `refs dangling` | refs | **missing** | add a dangling-references screen |
| `name lint` | naming | **missing** | add naming-template lint screen |
| `name rename` | naming | `r` rename | choose which entry -> #89 |
| `name apply` (scheme) | naming | **missing** | add bulk reference-aware rename-to-scheme |
| `rule` (field edits) | rule_edit | `e` rule | ok |
| `set` (create/update) | crud | **missing** | object creation -> #94 |
| `audit overlaps` | audit | `a` audit | ok |
| `decommission` | decommission | `x` decommission | ok |
| `move` | move | `m` move | destination picker -> #87 |
| `profile` / `init` / `login` | config/source | **missing** | manage/bootstrap profiles from TUI -> #83 |
| `version` | — | n/a | CLI-only, not needed in TUI |
| `workbench`/`w` | — | n/a | is the TUI |

## Net-new screens still needed
- Object creation (`set`) — #94
- `refs unused`
- `refs dangling`
- `name lint`
- `name apply` (rename-to-scheme) — related CLI backlog #15
- Profile management / bootstrap — #83

## Cross-cutting (already filed)
Value in find results #84 · dedup keep/drop + DG #85 · usage all-selected #86 · move destination #87 · staged changelist view/drop #88 · rename choose entry #89 · remove-from-selection #91 · partial config output #92 · SET-mode file output #93 · object creation #94.

**Principle:** reuse the `psc/core` engines verbatim; never reimplement validation or planning in the TUI. Every mutation stages a `ChangeSet` and honors `blockers`.
