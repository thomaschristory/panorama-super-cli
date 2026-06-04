# where-used across ALL rulebases — design (issue #2)

## Problem

`psc`'s reference graph only scans two rulebases: `security` and `nat`. PAN-OS
carries object references in **nine more** rulebases — PBF, decryption,
authentication, QoS, application-override, DoS, SD-WAN, tunnel-inspect, and
network-packet-broker. Until these are scanned:

- **`unused` lies.** An address referenced *only* by a QoS or PBF rule is
  reported as unused — and could then be deleted. `reachable_targets()` seeds
  only from `security-rule`/`nat-rule` references, so every other rulebase is
  invisible to reachability.
- **`merge`/`rename`/`delete` are unsafe.** They repoint *known* references
  before removing an object. A reference in an unscanned rulebase is silently
  left dangling — the exact failure repoint-before-delete exists to prevent.
- **`where-used` is incomplete** as a delete/rename pre-flight.

Scope decisions (confirmed with the user):

1. **All nine** object-referencing rulebases, not just the seven in the issue
   (tunnel-inspect + network-packet-broker added — the registry makes them
   nearly free, and omitting them leaves the same safety hole the issue exists
   to close).
2. **PBF nexthop** address-object references are modelled as a *review-gated*
   reference: shown in `where-used`, but a hard **blocker** on merge/rename
   (the appliers can't rewrite a nested single-value field), exactly like NAT
   translation fields.
3. **Additive** model: `SecurityRule`/`NatRule` are left untouched (zero
   regression risk on the safety-critical paths). One new generic `PolicyRule`
   + a tiny rulebase registry covers the nine new rulebases.
4. **Reference surface** per rulebase: address objects in `source`/`destination`,
   service objects in `service`, and rule `tag`s. Applications, zones, profiles,
   URL categories, and schedules are out of scope (none are psc-managed object
   kinds).

## Key insight

`referrer_kind` is always `"{rule_type}-rule"`, and the XML container tag, the
`set` keyword, and the live xpath segment are all just `rule_type`:
`security-rule → security`, `pbf-rule → pbf`, even `nat-rule → nat`. So a single
helper — `rule_container(referrer_kind) -> str | None` — collapses the four
hardcoded switch sites (`setcmd`, `apply_xml`, `apply_live`,
`changeset.reference_edit_is_mappable`) into table-driven code. Adding the
twelfth rulebase later becomes a one-line registry entry.

## Architecture

A new `psc/core/rulebases.py` is the single source of truth for *which*
rulebases carry references and *which* of their fields are flat (repointable)
vs nested (review-gated). Everything else consults it.

```
parse.py ──► PolicyRule (new model) ──► Snapshot.policy_rules
                                              │
refs.py._walk ──────────────────────────────►│  emits References (addr/svc/tag + pbf nexthop)
refs.py._rule_seeded_targets ────────────────►│  seeds reachability from ALL rulebases  ← unused fix
                                              │
dedup.field_members ─────────────────────────►│  resolves current member list for any *-rule
changeset.reference_edit_is_mappable ─────────►│  uses rule_container() + FLAT_RULE_FIELDS
setcmd / apply_xml / apply_live ─────────────►│  build path from rule_container()
```

### New module: `psc/core/rulebases.py`

```python
# rule_type value == XML container tag == `set` keyword == xpath segment.
POLICY_RULE_TYPES = (
    "pbf", "decryption", "authentication", "qos", "application-override",
    "dos", "sdwan", "tunnel-inspect", "network-packet-broker",
)
# Every referrer_kind that maps to a `{rb}-rulebase/{container}/rules` entry.
_RULE_CONTAINERS = {"security", "nat", *POLICY_RULE_TYPES}
# Flat <member> fields an applier can rewrite in place.
FLAT_RULE_FIELDS = frozenset({"source", "destination", "service", "tag"})

def rule_container(referrer_kind: str) -> str | None:
    """'pbf-rule' -> 'pbf'; None if not a rulebase referrer (group/object)."""
```

`FLAT_RULE_FIELDS` deliberately excludes `application`/`source-user` (never
emitted as object reference-edits) and PBF `nexthop` (nested → review-gated).
NAT keeps its existing `field in ("source","destination")` narrowing in the
appliers, since its translation fields are nested.

### New model: `psc/core/models.py`

```python
class RuleType(str, Enum):
    PBF = "pbf"; DECRYPTION = "decryption"; AUTHENTICATION = "authentication"
    QOS = "qos"; APPLICATION_OVERRIDE = "application-override"; DOS = "dos"
    SDWAN = "sdwan"; TUNNEL_INSPECT = "tunnel-inspect"
    NETWORK_PACKET_BROKER = "network-packet-broker"

class PolicyRule(BaseModel):
    """Reference surface of a 'security-shaped' rulebase (no NAT translation).

    One model for nine rulebases: they share source/destination (address),
    an optional service list, rule tags, and — for PBF only — a forwarding
    nexthop address object. Mirrors SecurityRule's 'only the reference surface'
    philosophy; application/source-user are omitted because they name no
    psc-managed object.
    """
    name: str
    location: Location = SHARED
    rulebase: Rulebase = Rulebase.PRE
    rule_type: RuleType
    source: list[str] = Field(default_factory=lambda: ["any"])
    destination: list[str] = Field(default_factory=lambda: ["any"])
    service: list[str] = Field(default_factory=list)   # empty for app-override
    nexthop: str | None = None                          # PBF forwarding object
    disabled: bool = False
    tags: list[str] = Field(default_factory=list)

    @property
    def referrer_kind(self) -> str: return f"{self.rule_type.value}-rule"
    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.location.name, self.rule_type.value, self.rulebase.value, self.name)
```

`Snapshot` gains `policy_rules: list[PolicyRule] = Field(default_factory=list)`.

### Parsing: `psc/core/parse.py`

`_collect` already loops `(pre-rulebase, post-rulebase)`. Add one call per
rulebase tag:

```python
for rt in RuleType:                       # drives off the registry
    snap.policy_rules.extend(_parse_policy_rules(rb_el, loc, rt))
```

`_parse_policy_rules` finds `./{rt.value}/rules/entry` and reads `source`,
`destination`, `service`, `tag` with the existing `_members` helper (absent →
empty, so app-override naturally has no service). For PBF it additionally reads
`action/forward/nexthop/fqdn` (the object-capable nexthop variant; a literal
`ip-address` nexthop is *not* an object and is ignored).

### Reference walk: `psc/core/refs.py`

- `_walk` gains one loop over `snap.policy_rules` emitting `source`/`destination`
  (address), `service` (service), `tag` (tag), and — when `rule_type is PBF and
  nexthop` — a `nexthop` (address) reference.
- `_rule_seeded_targets`: replace the `referrer_kind in ("security-rule",
  "nat-rule")` test with `rule_container(referrer_kind) is not None`. **This is
  the `unused` safety fix** — reachability now seeds from every rulebase.
- `dangling()`: exclude `field == "nexthop"` unresolved refs. A PBF nexthop is
  often a literal FQDN/IP, not an object; flagging those as dangling would be
  noise. (When it *does* resolve to an object, it appears in `where_used` and is
  gated on merge/rename — the safety win is preserved.)

### Repoint resolution: `psc/core/dedup.py::field_members`

Add an `elif rule_container(ref.referrer_kind) ...` branch that looks the rule
up in `snap.policy_rules` by `(rule_type, name, location, rulebase)` and returns
`getattr(rule, field)`. The existing security/nat branches stay.

### Safety gate + appliers (the four switch sites)

All four now compute the container via `rule_container()`:

- **`changeset.reference_edit_is_mappable`**: `address-group`/`service-group` →
  True; else `container = rule_container(kind)`; None or no `rulebase` → False;
  `nat` → `field in (source,destination)`; otherwise `field in FLAT_RULE_FIELDS`.
  PBF `nexthop` (not in the set) → False → `gate_unmappable_reference_edits`
  promotes it to a blocker when a delete/rename is present. **No new gating code
  is needed — the existing gate already does the right thing** once
  `reference_edit_is_mappable` returns False for nexthop.
- **`apply_xml._referrer_field_element`**, **`apply_live._referrer_field_xpath`**,
  **`setcmd._referrer_path`**: groups unchanged; rules build
  `{rb}-rulebase/{container}/rules/entry/{field}` (XML / xpath) or
  `{scope} {rb}-rulebase {container} rules {name} {field}` (set) from
  `rule_container()`. NAT translation + PBF nexthop fall through to the existing
  `None`/`# REVIEW` path.

The security and NAT behaviours are byte-for-byte identical (container values
`security`/`nat`), so the existing applier/setcmd/round-trip tests are the
regression guard.

### CLI / output

No changes. `refs_cmds` passes `referrer_kind` through as a string, so the new
kinds (`pbf-rule`, `qos-rule`, …) surface in `where-used`/`dangling` tables
automatically with distinct, self-describing labels.

## Testing (TDD — failing test first)

New fixture `tests/fixtures/all-rulebases.xml`: one rule per new rulebase across
`shared` + a device-group, pre and post, referencing shared and DG-local
addresses/services/tags, plus a PBF rule whose nexthop names an address object.

- **test_parse**: all nine rule_types parsed; fields correct; app-override has
  empty service; PBF nexthop captured.
- **test_refs**: `where_used` on an address used only by a QoS rule finds it;
  `unused` does **not** report an address used only by a DoS/PBF/SD-WAN rule
  (seeding fix); a bad service name in a decryption rule shows in `dangling`;
  a PBF nexthop object shows in `where_used`; an unresolved nexthop does **not**
  show in `dangling`.
- **test_dedup**: merging an address referenced by SD-WAN + authentication rules
  repoints both before delete; merging an address that is a PBF nexthop →
  **blocker** (review-gated), zero ops.
- **test_naming**: renaming an address referenced by a tunnel-inspect rule
  repoints it.
- **test_apply_xml**: reference edit into a decryption/QoS rule rewrites the
  member list and round-trips.
- **test_live_apply**: xpath for a network-packet-broker rule field is correct;
  a PBF nexthop edit is skipped (`None`), not mis-addressed.
- **test_setcmd**: `set`/`delete` lines for an SD-WAN reference edit; PBF nexthop
  edit renders `# REVIEW`.

## Docs to update

- `docs/getting-started/concepts.md` (reference-graph coverage sentence).
- `docs/guides/references-and-audit.md` (lines 16, 63-64 — drop the "other
  rulebases not yet covered" note).
- `docs/guides/duplicates-and-merging.md` (the repointed-fields list).
- `skills/panorama-super-cli/SKILL.md` (refs coverage).
- `refs.py` / `models.py` module docstrings.

## Out of scope / follow-ups

- Applications, zones, profiles, URL categories, schedules as reference kinds
  (would need new object models).
- Live read-modify-write for in-place updates (pre-existing limitation).
- Literal-IP PBF nexthop (only the object-capable `fqdn` variant is modelled).
```
