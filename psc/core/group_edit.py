"""Plan edits to a group's membership: one member added/removed, or a whole
group created from a set of objects.

The group analogue of `rule_edit`: PAN-OS `set … static [ x ]` *appends* to a
group's member list, so a naive add can't remove and isn't idempotent across
re-runs. `plan_group_member_edit` computes the full before/after member list and
emits a single `ReferenceEdit`, which `setcmd` renders as `delete <path> <leaf>`
+ `set <path> <leaf> [ …after ]` (idempotent) and both appliers express as a
wholesale member-field rewrite. Adding a present member or removing an absent
one collapses to an empty `ChangeSet` — re-running any op is a no-op.

`plan_group_create` builds a new group out of objects the operator has already
picked out (the workbench's `N` spoke). Because it takes *identified* members —
not the bare names `crud` takes — it can check each one against the group's
location: a member outside the location's visibility cone would dangle, and a
member whose name is shadowed from that location would bind to the wrong object.
Member references are bare names resolved upward, so neither is expressible away;
both are blockers.

Framework-free: they return a `ChangeSet`; the CLI formats and applies it.
"""

from __future__ import annotations

from collections.abc import Iterable

from psc.core import crud
from psc.core.changeset import ChangeSet, ReferenceEdit
from psc.core.models import SHARED, AddressGroup, Location, ServiceGroup, Snapshot
from psc.core.refs import ReferenceGraph, Target
from psc.output.errors import ErrorType, PscError

_GROUP_KINDS: frozenset[str] = frozenset({"address-group", "service-group"})

# The two PAN-OS object namespaces a group can be built from. Names resolve
# within a namespace, so an address and an address-group can shadow each other
# but never a service.
_ADDRESS_MEMBERS: frozenset[str] = frozenset({"address", "address-group"})
_SERVICE_MEMBERS: frozenset[str] = frozenset({"service", "service-group"})


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


def _group_kind_for(members: list[Target]) -> str:
    """The one group kind that can hold every member, or raise."""
    kinds = {m.kind for m in members}
    ungroupable = kinds - _ADDRESS_MEMBERS - _SERVICE_MEMBERS
    if ungroupable:
        raise PscError(
            f"{', '.join(sorted(ungroupable))} objects cannot be group members — "
            "select addresses/address-groups or services/service-groups",
            ErrorType.VALIDATION,
        )
    if kinds & _ADDRESS_MEMBERS and kinds & _SERVICE_MEMBERS:
        raise PscError(
            "the selection mixes addresses and services — no group kind holds both",
            ErrorType.VALIDATION,
        )
    return "address-group" if kinds & _ADDRESS_MEMBERS else "service-group"


def _member_blocker(
    graph: ReferenceGraph, namespace: str, member: Target, location: Location
) -> str | None:
    """Why `member` cannot be named by a group at `location`, if it cannot.

    A group's member list holds bare names, resolved upward from the group's own
    location. So the only question that matters is: from `location`, what does
    this member's *name* resolve to? Anything other than the member itself means
    the written group would not contain what the operator picked.
    """
    landed = graph.resolve(namespace, member.name, location)
    if landed is None:
        return (
            f"member '{member.name}' @{member.location.name} is not visible from "
            f"{location.name} — a group can only name objects in its own location, "
            "its ancestors, or shared"
        )
    if landed.location.name != member.location.name:
        return (
            f"member '{member.name}' @{member.location.name} is shadowed from "
            f"{location.name} by the {landed.kind} of the same name "
            f"@{landed.location.name} — PAN-OS resolves a member name upward, so the "
            "group would bind to that object instead"
        )
    if landed.kind != member.kind:
        # Same name, same location, different kind: the config already collides
        # inside one namespace, so which object the name reaches is not ours to
        # guess.
        return (
            f"member '{member.name}' @{member.location.name} is a {member.kind}, but "
            f"the name also denotes a {landed.kind} there — resolve the collision first"
        )
    return None


def _shadow_warning(snapshot: Snapshot, kind: str, name: str, location: Location) -> str | None:
    """The new group's name is already taken elsewhere in the same namespace."""
    namespace = _ADDRESS_MEMBERS if kind == "address-group" else _SERVICE_MEMBERS
    elsewhere = sorted(
        {
            obj.location.name
            for obj_kind, objs in (
                ("address", snapshot.addresses),
                ("address-group", snapshot.address_groups),
                ("service", snapshot.services),
                ("service-group", snapshot.service_groups),
            )
            if obj_kind in namespace
            for obj in objs
            if obj.name == name and obj.location.name != location.name
        }
    )
    if not elsewhere:
        return None
    return (
        f"'{name}' is already defined at {', '.join(elsewhere)} — the new group "
        f"@{location.name} and those objects shadow each other, and a bare reference "
        "resolves to whichever is nearest"
    )


def plan_group_create(
    snapshot: Snapshot,
    name: str,
    location: Location,
    members: list[Target],
    *,
    description: str | None = None,
    tags: list[str] | None = None,
) -> ChangeSet:
    """Plan a new static group at `location` whose members are `members`.

    The group kind follows the members: addresses/address-groups make an
    address-group, services/service-groups a service-group. The upsert itself is
    `crud`'s, so name/description/tag validation and the cross-kind namespace
    collision blocker are reused verbatim.

    Raises `PscError` (VALIDATION) on input no group can express: an empty
    selection, a member kind that cannot belong to a group, a selection spanning
    both namespaces, a member sharing the group's own name (a group cannot
    contain itself, whatever the location), or a description on a service-group
    (PAN-OS has no such field).

    Blocks — rather than silently writing a broken reference — when a member is
    invisible or shadowed from `location` (see `_member_blocker`), or when a group
    of that kind already exists at that name and location (this creates; growing a
    group is `plan_group_member_edit`).
    """
    if not members:
        raise PscError("select at least one object to build a group from", ErrorType.VALIDATION)
    kind = _group_kind_for(members)
    if any(m.name == name for m in members):
        # Not just self-selection: a group named `web` @dg-a whose member list says
        # `web` resolves that member to the group itself, wherever the address the
        # operator picked actually lives.
        raise PscError(f"a group cannot contain itself ('{name}')", ErrorType.VALIDATION)

    # Order-preserving dedup: the same name selected twice (two locations, one
    # shadowing the other) is one member on the device.
    member_names = list(dict.fromkeys(m.name for m in members))
    tags = list(tags or [])

    if kind == "address-group":
        cs = crud.plan_address_group(
            snapshot,
            name,
            static_members=member_names,
            dynamic_filter=None,
            description=description,
            tags=tags,
            location=location,
        )
        existing: list[AddressGroup] | list[ServiceGroup] = [
            g
            for g in snapshot.address_groups
            if g.name == name and g.location.name == location.name
        ]
        namespace = "address"
    else:
        if description is not None:
            raise PscError("a service-group has no description field", ErrorType.VALIDATION)
        cs = crud.plan_service_group(snapshot, name, member_names, tags=tags, location=location)
        existing = [
            g
            for g in snapshot.service_groups
            if g.name == name and g.location.name == location.name
        ]
        namespace = "service"

    if existing:
        cs.blockers.append(
            f"{kind} '{name}' @{location.name} already exists — this creates a group; "
            "add members to an existing one with `psc group edit-member --add`"
        )

    graph = ReferenceGraph.build(snapshot)
    for member in members:
        blocker = _member_blocker(graph, namespace, member, location)
        if blocker is not None:
            cs.blockers.append(blocker)

    warning = _shadow_warning(snapshot, kind, name, location)
    if warning is not None:
        cs.warnings.append(warning)
    return cs


def suggest_group_location(snapshot: Snapshot, member_locations: Iterable[str]) -> str | None:
    """The narrowest location a group holding members at `member_locations` can
    live in, or `None` when no location can see them all.

    "Narrowest" is the candidate whose visibility cone — itself, its ancestors,
    shared — contains every member's location and is smallest; ties break
    alphabetically. Members in sibling device-groups have no such location: no
    scope in PAN-OS sees into two branches at once.

    A suggestion only, driving the location picker's default. `plan_group_create`
    is what actually refuses an unreachable member.
    """
    wanted = set(member_locations)
    best: tuple[int, str] | None = None
    for candidate in ("shared", *snapshot.device_groups):
        loc = SHARED if candidate == "shared" else Location.dg(candidate)
        cone = snapshot.visible_location_names(loc) or set()
        if wanted <= cone and (best is None or (len(cone), candidate) < best):
            best = (len(cone), candidate)
    return best[1] if best is not None else None


__all__ = ["plan_group_create", "plan_group_member_edit", "suggest_group_location"]
