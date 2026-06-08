# Coverage and blind spots

!!! danger "Read this before you delete anything based on `unused`"
    `psc`'s `unused` result means **"unreferenced by the object groups and
    policy rulebases in the config you handed it"** — *not* "safe to delete from
    Panorama." Several places that legitimately reference an object are **not
    scanned at all**, so an object that is genuinely in use can be reported
    unused. Deleting it then breaks something with no warning.

    The merge/rename **repointing** is trustworthy for the reference sites psc
    *does* scan (it repoints them before deleting, and blocks when it can't).
    The risk is the sites it never sees — listed below.

This page is the authoritative map of what the reference graph (`refs used`,
`refs unused`, `refs dangling`) and the audit/dedup engines do and do not look
at. Keep it open when you act on a finding.

## What psc scans

`psc` parses a Panorama **config export** (or the live running config) and reads
only:

- `shared` and each **device-group**'s objects: addresses, address-groups,
  services, service-groups, tags.
- Each device-group's **policy rulebases**, pre- and post-: `security`, `nat`,
  and (since v0.3.0) `pbf`, `decryption`, `authentication`, `qos`,
  `application-override`, `dos`, `sdwan`, `tunnel-inspect`,
  `network-packet-broker`.

Within those rulebases it models exactly this **reference surface**:

| Field | Namespace | Scanned |
|-------|-----------|---------|
| `source` / `destination` | address (+ address-group) | ✅ all rulebases |
| `service` | service (+ service-group) | ✅ all rulebases that have it |
| rule `tag` | tag | ✅ all rulebases (security, NAT, and the 9) |
| NAT `source-translation` / `destination-translation` | address | ✅ where-used; **review-gated** for repoint |
| PBF forwarding `nexthop` (`fqdn` variant) | address | ✅ where-used; **review-gated** for repoint |
| static address-group / service-group members | address / service | ✅ |
| dynamic address-group (DAG) membership | address | ✅ from **config tags**; registered IPs not covered (see below) |

"Review-gated" means psc *sees* the reference and will **block** a
merge/rename/delete that would strand it (it cannot rewrite a nested,
non-member-list field automatically) — a loud refusal, never a silent break.

## What psc does NOT scan

Ordered by how badly each can hurt you.

### 1. Templates and network/device config — not parsed at all

psc reads device-group **objects and policy** only. It never opens
**templates / template-stacks** or the network/device config. So an address
object referenced by any of these reads as **unused**:

- IKE / IPSec gateway peer addresses,
- GlobalProtect portal / gateway addresses,
- service routes, DNS proxy, virtual-router static routes, interface addresses,
- log-forwarding / SNMP / syslog server destinations,
- any address used purely in network or device settings.

Deleting such an object because psc called it "unused" can break VPN,
management, routing, or logging — with no warning, because psc literally never
saw the reference. **This is the most dangerous gap.** Treat every `unused`
result on a **shared** object as "unused by policy," and verify in Panorama
before deleting.

### 2. Dynamic address groups (DAGs): only config-tag membership is resolved

A DAG includes addresses by a **tag expression** (e.g. `'prod' and 'web'`), not
a static member list. Since v0.4.3 psc **evaluates that filter against the
static tags it already parses**, so an address whose only use is being matched
into a rule-referenced DAG is treated as reachable — it is no longer reported
`unused`, and `refs used <addr>` shows the DAG (as a `dynamic` referrer) on the
path to the rule. DAG filters are also still parsed for the unused-**tag** check.

The residual gap is **runtime, not config**: an address pulled into a DAG by an
**externally registered IP** (XML-API / User-ID / VM-info / cloud plugin) carries
no config tag, so the export psc reads cannot show that membership. Such an
address can still be reported `unused`. Only a **live** membership query
(`show object dynamic-address-group all`) sees registered IPs; resolving them is
tracked as a follow-up enhancement on the live path.

If a DAG's filter is **malformed/unparseable**, psc does not guess its
membership (it matches nothing) and prints a `warning` on stderr naming that DAG,
so you know its coverage is unverified.

### 3. Whole object categories psc does not model

These are not object kinds psc tracks, so it can neither tell you what
references them nor follow references *to* them:

- security / decryption / DoS **profiles** and **profile-groups**,
- **schedules**,
- **external dynamic lists (EDLs)**,
- **applications** and **application-groups** (including custom apps),
- **custom URL categories**, **regions**, **HIP profiles**, **user-groups**.

Two consequences: (a) renaming/deleting one of these is outside psc's safety net
entirely; (b) when one of these names appears in a rule field psc *does* read
(e.g. an EDL or region in `source`), it won't resolve to an address/service and
will surface as a **`dangling` false-positive** — and it never seeds
reachability, so it can't keep a *different* object from looking unused.

### 4. The config is a single snapshot

Findings reflect the one export/device you pointed psc at. References from
**another Panorama**, from **pushed templates**, or from **firewall-local
config** (rules a firewall has that aren't in Panorama) are invisible. A shared
object that looks unused here may be referenced by a managed firewall's local
config.

## Annoying but safe (won't cause a bad delete)

- **Disabled rules count as "used."** psc seeds reachability from disabled rules
  too, so an object used only by a disabled rule is **not** reported unused.
  Safe from deletion — but you also can't easily *find* such objects to clean
  up. (Tracked separately.)
- **`dangling` noise from built-ins.** Only `any`, `application-default`,
  `service-http`, `service-https`, and `service-dns` are whitelisted. Other
  built-in services, regions, or EDL names in a rule will be reported
  `dangling` even though they are valid. Skim `dangling` output for these
  before treating it as an error list.
- **`find ip` is literal, not DNS-aware.** It won't match an FQDN object that
  *resolves* to the target IP. **`dedup`** only compares **addresses and
  services** — not groups or tags.

## Rule of thumb

1. `refs used` (where-used) is reliable **for the rulebases psc scans** — it is
   the right delete/rename pre-flight, and merge/rename repoint those sites or
   block.
2. `refs unused` is a **candidate list, not a kill list**, especially for
   `shared` objects. Before deleting, ask: could this live in a template, in
   network/VPN/management config, in a DAG via an externally registered IP, or
   on a firewall's local config? If plausibly yes, confirm in Panorama first.
3. The safe operations are the ones psc can fully model and *block* when it
   can't (merge, rename). The risky operation is **deletion driven by
   `unused`**, because that is exactly where an unseen reference turns into an
   outage.

## Tracking

`refs unused` prints a one-line caveat to **stderr** restating these blind spots
at the point of use (stdout stays pure machine output). The remaining gaps —
DAG membership from externally registered IPs (the live-path enhancement),
parsing template/network references, modelling more object kinds — are tracked in
the issue tracker. See
[github.com/thomaschristory/panorama-super-cli/issues](https://github.com/thomaschristory/panorama-super-cli/issues).
