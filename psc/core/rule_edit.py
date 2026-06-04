"""Plan an idempotent add/remove of one member of a rule's reference field.

PAN-OS `set ... <field> [ x ]` *appends* to a member-list field, so a naive
"add member" set command can't remove and is not idempotent across re-runs.
This engine sidesteps that entirely: it computes the full before/after member
list and emits a single `ReferenceEdit`, which `setcmd` renders as
`delete <path> <field>` + `set <path> <field> [ ...after ]` (idempotent) and
both appliers express as a wholesale field rewrite. An add of a member already
present, or a remove of one already absent, collapses to an empty ChangeSet —
re-running any op is a no-op.

Framework-free: it returns a `ChangeSet`; the CLI formats and applies it.
"""

from __future__ import annotations

from psc.core.changeset import ChangeSet, ReferenceEdit, gate_unmappable_reference_edits
from psc.core.dedup import attr_as_members
from psc.core.models import (
    Location,
    NatRule,
    PolicyRule,
    Rulebase,
    RuleType,
    SecurityRule,
    Snapshot,
)
from psc.output.errors import ErrorType, PscError

# The member-list rule fields this command can edit. NAT's `service` is a scalar
# and NAT has no `application`; those are caught per-rule-type below.
_EDITABLE_FIELDS: frozenset[str] = frozenset({"source", "destination", "service", "application"})

# Policy rulebases that structurally have NO `service` field. Membership here —
# not an empty `service` list — is what makes a `service` edit illegal, because
# an empty list is also the normal unconfigured state of a service-bearing rule.
# application-override is the only one of the nine `RuleType` rulebases that
# omits service entirely; the rest (qos/pbf/dos/sdwan/tunnel-inspect/
# authentication/decryption/network-packet-broker) all carry a service list.
_NO_SERVICE_RULE_TYPES: frozenset[RuleType] = frozenset({RuleType.APPLICATION_OVERRIDE})


def _candidates(
    snapshot: Snapshot, rule_name: str, rulebase: Rulebase
) -> list[tuple[str, str, SecurityRule | NatRule | PolicyRule]]:
    """Every rule named `rule_name` in `rulebase`, as (kind, location_name, rule)."""
    out: list[tuple[str, str, SecurityRule | NatRule | PolicyRule]] = []
    for r in snapshot.security_rules:
        if r.name == rule_name and r.rulebase == rulebase:
            out.append(("security-rule", r.location.name, r))
    for n in snapshot.nat_rules:
        if n.name == rule_name and n.rulebase == rulebase:
            out.append(("nat-rule", n.location.name, n))
    for p in snapshot.policy_rules:
        if p.name == rule_name and p.rulebase == rulebase:
            out.append((p.referrer_kind, p.location.name, p))
    return out


def _rewrite(before: list[str], *, add: str | None, remove: str | None) -> list[str]:
    if add is not None:
        if add in before:
            return list(before)
        return [*before, add]
    after: list[str] = [m for m in before if m != remove]
    return after


def plan_rule_member_edit(
    snapshot: Snapshot,
    rule_name: str,
    location: Location | None,
    rulebase: Rulebase,
    field: str,
    *,
    add: str | None,
    remove: str | None,
) -> ChangeSet:
    """Plan adding or removing one member of a rule's `field` (idempotent).

    Resolves the rule by (name, location, rulebase) across security, then NAT,
    then policy rulebases. Raises `PscError`:
      - NOT_FOUND when no such rule exists;
      - VALIDATION on an unknown field, an ambiguous rule (same name in multiple
        locations when `location` isn't specific), or a field invalid for the
        rule type (NAT `application`, application-override `service`).
    A NAT scalar `service` edit is a hard *blocker* (the field is a member-list
    on other rule types, so it's a per-instance refusal, not a bad-input error).
    An add of a present member / remove of an absent one returns an empty plan.
    """
    if field not in _EDITABLE_FIELDS:
        raise PscError(
            f"field '{field}' is not an editable member list "
            f"(choose one of: {', '.join(sorted(_EDITABLE_FIELDS))})",
            ErrorType.VALIDATION,
        )

    matches = _candidates(snapshot, rule_name, rulebase)
    if location is not None:
        matches = [m for m in matches if m[1] == location.name]
    if not matches:
        raise PscError(
            f"no {rulebase.value}-rulebase rule named '{rule_name}'"
            + (f" @{location.name}" if location is not None else ""),
            ErrorType.NOT_FOUND,
        )

    locations = {m[1] for m in matches}
    if len(locations) > 1:
        raise PscError(
            f"rule '{rule_name}' is ambiguous — found in {len(locations)} locations; "
            "pass --location to disambiguate",
            ErrorType.VALIDATION,
            details={"candidates": [{"kind": k, "location": loc} for k, loc, _ in matches]},
        )

    cs = ChangeSet(
        title=(
            f"edit-member {field} of {matches[0][0]} '{rule_name}' "
            f"@{matches[0][1]} {rulebase.value}"
        )
    )

    if len(matches) > 1:
        # Same name across >1 collection at one location (malformed export):
        # take the first deterministically and warn.
        cs.warnings.append(
            f"rule '{rule_name}' matches {len(matches)} rule kinds at @{matches[0][1]}; "
            f"editing the first ({matches[0][0]})"
        )

    kind, loc_name, rule = matches[0]

    # Per-rule-type field legality.
    if isinstance(rule, NatRule):
        if field == "application":
            raise PscError(f"nat rule '{rule_name}' has no application field", ErrorType.VALIDATION)
        if field == "service":
            cs.blockers.append(
                f"nat rule '{rule_name}' service is a scalar field, not a member list — "
                "edit it directly (it names a single service or 'any')"
            )
            return cs
    elif isinstance(rule, PolicyRule):
        # Only SecurityRule models `application`; a policy rule has no such field,
        # so an edit would invent an `<application>` element PAN-OS rejects.
        if field == "application":
            raise PscError(
                f"rule '{rule_name}' ({rule.referrer_kind}) has no application field",
                ErrorType.VALIDATION,
            )
        # Gate `service` on rule TYPE, not emptiness: a service-bearing rulebase
        # with no service configured has an empty list, and adding the first
        # member must work. Only rulebases that structurally omit the field refuse.
        if field == "service" and rule.rule_type in _NO_SERVICE_RULE_TYPES:
            raise PscError(
                f"rule '{rule_name}' ({rule.referrer_kind}) has no service field",
                ErrorType.VALIDATION,
            )

    before = attr_as_members(rule, field)
    after = _rewrite(before, add=add, remove=remove)
    if after == before:
        return cs  # idempotent no-op

    cs.reference_edits.append(
        ReferenceEdit(
            referrer_kind=kind,
            referrer_name=rule_name,
            referrer_location=loc_name,
            field=field,
            rulebase=rulebase.value,
            before=before,
            after=after,
        )
    )
    # Defensive: these fields are all mappable, so this is a no-op, but it keeps
    # the safety invariant uniform with merge/rename.
    gate_unmappable_reference_edits(cs)
    return cs


__all__ = ["plan_rule_member_edit"]
