"""The catalogue of PAN-OS rulebases `psc` scans for object references.

Every "security-shaped" rulebase — `security` plus the nine added in v0.2 —
shares one reference surface: `source`/`destination` address members, an
optional `service` member list, and rule `tag`s, all flat `<member>` lists
addressed identically in XML, on the wire, and in `set` syntax. The single
fact that makes this tractable: a rule's `referrer_kind` is always
`"{tag}-rule"`, and that *tag* is simultaneously the XML container element, the
`set` keyword, and the live xpath segment (`pbf-rule` → `pbf`, even
`nat-rule` → `nat`).

So this module is the one place that knows the rulebase tags. The parser, the
reference walk, and all three appliers (`setcmd`, `apply_xml`, `apply_live`)
derive everything from `rule_container(...)` instead of growing a parallel
if/elif per rulebase. Adding the next rulebase is a one-line edit here.
"""

from __future__ import annotations

# rule_type value == XML container tag == `set` keyword == live xpath segment.
# The nine rulebases added in v0.2 (security and nat predate this catalogue and
# keep their own model classes, but resolve through `rule_container` too).
POLICY_RULE_TYPES: tuple[str, ...] = (
    "pbf",
    "decryption",
    "authentication",
    "qos",
    "application-override",
    "dos",
    "sdwan",
    "tunnel-inspect",
    "network-packet-broker",
)

# Every referrer_kind that resolves to a `{rb}-rulebase/{container}/rules` entry.
_RULE_CONTAINERS: frozenset[str] = frozenset({"security", "nat", *POLICY_RULE_TYPES})

# Flat `<member>` rule fields a merge/rename can rewrite in place. Excludes
# NAT translation fields and the PBF `nexthop` (nested → review-gated), and
# `application`/`source-user` (never named in an object reference-edit).
FLAT_RULE_FIELDS: frozenset[str] = frozenset({"source", "destination", "service", "tag"})

_SUFFIX = "-rule"


def rule_container(referrer_kind: str) -> str | None:
    """The XML/`set`/xpath container tag for a rulebase referrer, else `None`.

    `'pbf-rule' -> 'pbf'`; `None` for object referrers (`address-group`,
    `service-group`, `address`, …) and for any unknown `*-rule` base.
    """
    if not referrer_kind.endswith(_SUFFIX):
        return None
    container = referrer_kind[: -len(_SUFFIX)]
    return container if container in _RULE_CONTAINERS else None
