"""Promote a whole duplicate bucket toward shared, reference-safely (issue #154).

`dedup` finds cross-device-group duplicates but cannot fix the canonical one —
the same object defined in DG-A and DG-B and nowhere in `shared`. `plan_merge`
has no upsert path, so its survivor must already be a bucket member, and that
survivor is invisible to the *other* device-group's referrers; the plan blocks.
`relocate` can promote, but one source at a time, with no idea a bucket exists.

`promote` is the missing operation: create the object **once** at a common
ancestor (usually `shared`), then delete every device-group copy. Because
promotion is upward-only, the deletes are the whole job — each reference falls
through, by ordinary PAN-OS shadowing, onto the promoted definition. The default
path therefore emits **zero** reference edits, exactly like `relocate`.

The per-object gates are relocate's (direction, intermediate-shadow, dependency
resolution), run once per bucket member. What this engine adds is bucket-level
and cannot live in either parent: value homogeneity across the bucket, the
divergent-name gate, ONE destination upsert shared by N sources (looping
relocate's per-object planner would emit N identical upserts, since the snapshot
is never mutated between them), and the sibling-shadow warning — `relocate`'s
guard walks only the ancestor chain, so it can never see a sibling device-group
that keeps shadowing the promoted name.
"""

from __future__ import annotations

from psc.core.changeset import ChangeSet, ObjectDelete, ObjectKind
from psc.core.dedup import ObjectRef
from psc.core.models import Address, AddressGroup, Location, Service, Snapshot
from psc.core.refs import ReferenceGraph
from psc.core.relocate import (
    NAMESPACE,
    Obj,
    build_destination_upsert,
    find_object,
    promotion_blocker,
    revive_warnings,
    same_value,
    to_location,
)
from psc.output.errors import ErrorType, PscError

# Exactly the kinds `dedup` buckets. Tags carry no value to deduplicate, and
# service-groups have no dedup finder, so neither has a bucket to promote.
PROMOTABLE_KINDS = frozenset({ObjectKind.ADDRESS, ObjectKind.SERVICE, ObjectKind.ADDRESS_GROUP})

# The concrete object types a promotable bucket can hold. Narrower than
# relocate's `Obj` (no Tag), which is what lets us read `.tags`/`.description`
# off a bucket member without a cast.
PromotableObj = Address | AddressGroup | Service

_Member = tuple[ObjectRef, PromotableObj]


def _blocked(cs: ChangeSet) -> ChangeSet:
    """A blocked plan carries zero ops and makes no claims (repo invariant).

    Warnings go too, for dedup's reason: they describe what applying *would* do,
    and a blocked plan is never applied — `complete()` puts them straight into the
    CONFLICT envelope, where they read as a contradiction.
    """
    cs.upserts.clear()
    cs.deletes.clear()
    cs.reference_edits.clear()
    cs.warnings.clear()
    return cs


def _rank(snapshot: Snapshot, m: ObjectRef) -> tuple[int, str, str]:
    """Depth from `shared`, then location, then name — dedup's survivor ordering.

    `ancestors()` is [self, …parents, shared], so `shared` is depth 0 and sorts
    first. The best-ranked member becomes the *template*: the copy whose
    description and tags the promoted object inherits.
    """
    return (len(snapshot.ancestors(m.loc)) - 1, m.location, m.name)


def _resolve_members(
    cs: ChangeSet, snapshot: Snapshot, kind: ObjectKind, members: list[ObjectRef]
) -> list[_Member]:
    """Rank-ordered (ref, object) pairs; a member that does not exist is a blocker."""
    out: list[_Member] = []
    for m in sorted(members, key=lambda r: _rank(snapshot, r)):
        obj = find_object(snapshot, kind, m.name, m.loc)
        if obj is None:
            cs.blockers.append(f"{kind.value} '{m.name}'@{m.location} does not exist")
            continue
        assert isinstance(obj, Address | AddressGroup | Service)
        out.append((m, obj))
    return out


def _drift_warnings(cs: ChangeSet, template: _Member, objs: list[_Member]) -> None:
    """Attributes a discarded copy carries that the promoted object will not.

    Tags are the sharp edge: they decide dynamic address-group membership, so a
    tag that exists only on a discarded copy silently changes what a DAG matches.
    Warn — the operator, not the tool, decides.
    """
    tpl_ref, tpl = template
    for m, obj in objs:
        if (m.name, m.location) == (tpl_ref.name, tpl_ref.location):
            continue
        lost = sorted(set(obj.tags) - set(tpl.tags))
        if lost:
            # No square brackets: warnings render through rich, which would eat
            # `[prod, dmz]` as markup and print nothing.
            cs.warnings.append(
                f"'{m.name}'@{m.location} has tags the promoted copy will not carry: "
                f"{', '.join(lost)}"
            )
        if obj.description and obj.description != tpl.description:
            cs.warnings.append(
                f"'{m.name}'@{m.location} has a description the promoted copy will not carry"
            )


def _sibling_shadow_warnings(
    cs: ChangeSet,
    snapshot: Snapshot,
    graph: ReferenceGraph,
    *,
    namespace: str,
    survivor: str,
    dest: Location,
    members: list[ObjectRef],
) -> None:
    """Warn about device-groups that will keep shadowing the promoted object.

    A definition of the same name in a device-group that is *not* in the bucket
    still captures its own subtree's references after the promotion. `relocate`'s
    intermediate-shadow guard walks only the ancestor chain between one source and
    the destination, so it structurally cannot see these — which is why an operator
    doing this by hand with two `move` runs is left shadowed with no warning.
    """
    member_locs = {m.location for m in members}
    for loc in snapshot.locations():
        if loc == dest or loc.name in member_locs:
            continue
        if dest not in snapshot.ancestors(loc):
            continue  # does not inherit the destination; nothing to shadow
        if graph.defined_at(namespace, survivor, loc):
            cs.warnings.append(
                f"device-group '{loc.name}' still defines '{survivor}' in the {namespace} "
                f"namespace; it will keep shadowing the {dest.name} copy for its own subtree"
            )


def _check_same_value(
    cs: ChangeSet, kind: ObjectKind, template: _Member, objs: list[_Member]
) -> None:
    """Every bucket member must carry the same match-affecting value.

    A merge (unlike a same-value collision at the destination) has no
    `--allow-value-change` escape hatch: promote never re-derives a canonical
    value, so divergent members are simply not one bucket.
    """
    template_ref, tpl = template
    for m, obj in objs[1:]:
        if not same_value(kind, tpl, obj):
            cs.blockers.append(
                f"'{m.name}'@{m.location} does not carry the same value as "
                f"'{template_ref.name}'@{template_ref.location}; this is not one bucket"
            )


def _check_per_member_gates(
    cs: ChangeSet,
    snapshot: Snapshot,
    graph: ReferenceGraph,
    *,
    kind: ObjectKind,
    namespace: str,
    dest: Location,
    objs: list[_Member],
) -> None:
    """Run relocate's direction/intermediate-shadow/dependency gates per source.

    A member already sitting at the destination is not a source: there is
    nothing to promote and nothing to shadow-check.
    """
    for m, obj in objs:
        if m.loc == dest:
            continue
        blocker = promotion_blocker(
            snapshot,
            graph,
            kind=kind,
            name=m.name,
            src_obj=obj,
            source=m.loc,
            dest=dest,
            namespace=namespace,
            check_dependencies=True,
        )
        if blocker is not None:
            cs.blockers.append(f"'{m.name}'@{m.location}: {blocker}")


def _plan_destination(
    cs: ChangeSet,
    snapshot: Snapshot,
    *,
    kind: ObjectKind,
    template: Obj,
    survivor: str,
    dest: Location,
) -> None:
    """Upsert `survivor` at `dest` — exactly once — or adopt the copy already there.

    Deliberately not `relocate._plan_one` in a loop: that re-checks the *unmutated*
    snapshot each time, so N sources would each find the destination empty and emit
    N identical upserts.
    """
    dest_obj = find_object(snapshot, kind, survivor, dest)
    if dest_obj is not None:
        if not same_value(kind, template, dest_obj):
            cs.blockers.append(
                f"destination {dest.name} already defines {kind.value} '{survivor}' with a "
                "different value; merge or rename one side first"
            )
            return
        cs.warnings.append(
            f"{dest.name} already defines {kind.value} '{survivor}' with an identical value; "
            "the device-group copies will be removed and references will resolve to it"
        )
        return

    obj = template if template.name == survivor else template.model_copy(update={"name": survivor})
    upsert_cs = build_destination_upsert(snapshot, kind, obj, dest)
    cs.upserts.extend(upsert_cs.upserts)
    cs.blockers.extend(upsert_cs.blockers)
    cs.warnings.extend(upsert_cs.warnings)


def plan_promote(
    snapshot: Snapshot,
    graph: ReferenceGraph,
    *,
    kind: ObjectKind,
    members: list[ObjectRef],
    dest_name: str = "shared",
) -> ChangeSet:
    """Plan promoting a whole duplicate bucket to `dest_name` (default `shared`).

    Returns a `ChangeSet`; any unsafe condition yields a blocked, zero-op plan. The
    bucket's members must all carry the same value and — in this phase — the same
    name; a member already sitting at the destination is adopted as the destination
    object rather than deleted.
    """
    if kind not in PROMOTABLE_KINDS:
        raise PscError(f"promote does not support {kind.value} objects", ErrorType.INPUT)
    if not members:
        raise PscError("empty duplicate bucket", ErrorType.INPUT)

    dest = to_location(dest_name)
    namespace = NAMESPACE[kind]
    cs = ChangeSet(title=f"promote {len(members)} {kind.value}(s) -> @{dest.name}")

    objs = _resolve_members(cs, snapshot, kind, members)
    if cs.blockers:
        return _blocked(cs)

    names = {m.name for m, _ in objs}
    if len(names) > 1:
        listed = ", ".join(f"'{m.name}'@{m.location}" for m, _ in objs)
        cs.blockers.append(
            f"bucket names diverge ({listed}); pass --keep NAME to unify them on one name"
        )
        return _blocked(cs)
    survivor = next(iter(names))
    _, template = objs[0]

    _check_same_value(cs, kind, objs[0], objs)
    if cs.blockers:
        return _blocked(cs)

    _check_per_member_gates(
        cs, snapshot, graph, kind=kind, namespace=namespace, dest=dest, objs=objs
    )
    if cs.blockers:
        return _blocked(cs)

    _plan_destination(cs, snapshot, kind=kind, template=template, survivor=survivor, dest=dest)
    if cs.blockers:
        return _blocked(cs)

    for m, _obj in objs:
        if m.loc == dest and m.name == survivor:
            continue  # this IS the destination object
        cs.deletes.append(ObjectDelete(kind=kind, name=m.name, location=m.location))

    _drift_warnings(cs, objs[0], objs)
    revive_warnings(cs, snapshot, graph, name=survivor, namespace=namespace, dest=dest)
    _sibling_shadow_warnings(
        cs,
        snapshot,
        graph,
        namespace=namespace,
        survivor=survivor,
        dest=dest,
        members=[m for m, _ in objs],
    )
    return cs
