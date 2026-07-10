"""Plan an idempotent add/remove of one member of an address- or service-group.

The group analogue of `rule_edit`: PAN-OS `set … static [ x ]` *appends* to a
group's member list, so a naive add can't remove and isn't idempotent across
re-runs. This engine computes the full before/after member list and emits a
single `ReferenceEdit`, which `setcmd` renders as `delete <path> <leaf>` +
`set <path> <leaf> [ …after ]` (idempotent) and both appliers express as a
wholesale member-field rewrite. Adding a present member or removing an absent
one collapses to an empty `ChangeSet` — re-running any op is a no-op.

Framework-free: it returns a `ChangeSet`; the CLI formats and applies it.
"""

from __future__ import annotations

from psc.core.changeset import ChangeSet, ReferenceEdit
from psc.core.models import AddressGroup, Location, ServiceGroup, Snapshot
from psc.output.errors import ErrorType, PscError

_GROUP_KINDS: frozenset[str] = frozenset({"address-group", "service-group"})


def _members_of(group: AddressGroup | ServiceGroup) -> list[str]:
    if isinstance(group, AddressGroup):
        return list(group.static_members or [])
    return list(group.members)


def _candidates(
    snapshot: Snapshot, group_name: str, kind: str | None
) -> list[tuple[str, str, AddressGroup | ServiceGroup]]:
    """Every group named `group_name`, as (kind, location_name, group)."""
    out: list[tuple[str, str, AddressGroup | ServiceGroup]] = []
    if kind in (None, "address-group"):
        for g in snapshot.address_groups:
            if g.name == group_name:
                out.append(("address-group", g.location.name, g))
    if kind in (None, "service-group"):
        for sg in snapshot.service_groups:
            if sg.name == group_name:
                out.append(("service-group", sg.location.name, sg))
    return out


def _rewrite(before: list[str], *, add: str | None, remove: str | None) -> list[str]:
    if add is not None:
        return list(before) if add in before else [*before, add]
    return [m for m in before if m != remove]


def plan_group_member_edit(
    snapshot: Snapshot,
    group_name: str,
    location: Location | None = None,
    *,
    add: str | None = None,
    remove: str | None = None,
    kind: str | None = None,
) -> ChangeSet:
    """Plan adding or removing one member of a group's member list (idempotent).

    Resolves the group by (name, [location], [kind]) across address-groups then
    service-groups. Raises `PscError`:
      - VALIDATION on a bad `kind`, a dynamic address-group (no static member
        list), a self-referential add, or an ambiguous group (same name in
        multiple locations, or as both group kinds — pass `location`/`kind`);
      - NOT_FOUND when no such group exists.
    An add of a present member / remove of an absent one returns an empty plan.
    """
    if kind is not None and kind not in _GROUP_KINDS:
        raise PscError(
            f"kind '{kind}' is not a group (choose address-group or service-group)",
            ErrorType.VALIDATION,
        )
    if add is not None and add == group_name:
        raise PscError(f"a group cannot contain itself ('{group_name}')", ErrorType.VALIDATION)

    matches = _candidates(snapshot, group_name, kind)
    if location is not None:
        matches = [m for m in matches if m[1] == location.name]
    if not matches:
        raise PscError(
            f"no group named '{group_name}'"
            + (f" ({kind})" if kind else "")
            + (f" @{location.name}" if location is not None else ""),
            ErrorType.NOT_FOUND,
        )

    locations = {m[1] for m in matches}
    if len(locations) > 1:
        raise PscError(
            f"group '{group_name}' is ambiguous — found in {len(locations)} locations; "
            "pass --location to disambiguate",
            ErrorType.VALIDATION,
            details={"candidates": [{"kind": k, "location": loc} for k, loc, _ in matches]},
        )
    kinds = {m[0] for m in matches}
    if len(kinds) > 1:
        raise PscError(
            f"'{group_name}' names both an address-group and a service-group "
            f"@{matches[0][1]}; pass --kind to disambiguate",
            ErrorType.VALIDATION,
        )

    match_kind, loc_name, group = matches[0]

    if isinstance(group, AddressGroup) and group.is_dynamic:
        raise PscError(
            f"address-group '{group_name}' @{loc_name} is dynamic (filter-based) and "
            "has no static member list to edit",
            ErrorType.VALIDATION,
        )

    verb = "add" if add is not None else "remove"
    member = add if add is not None else remove
    cs = ChangeSet(title=f"{verb}-member {member} of {match_kind} '{group_name}' @{loc_name}")

    before = _members_of(group)
    after = _rewrite(before, add=add, remove=remove)
    if after == before:
        return cs  # idempotent no-op

    # `field` is cosmetic for a group edit — every applier hardcodes the leaf
    # (`static` for address-group, `members` for service-group) — so "members"
    # keeps the plan summary readable for both kinds.
    cs.reference_edits.append(
        ReferenceEdit(
            referrer_kind=match_kind,
            referrer_name=group_name,
            referrer_location=loc_name,
            field="members",
            before=before,
            after=after,
        )
    )
    return cs


__all__ = ["plan_group_member_edit"]
