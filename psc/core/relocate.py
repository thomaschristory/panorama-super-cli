"""Promote an object *toward* shared, reference-safely (issue #74).

A "move" is create-at-destination + delete-at-source. `psc` restricts it to the
one direction that is safe without rewriting any reference: **toward shared** —
the destination must be `shared` or a strict ancestor device-group of the
source. In that direction the destination sits *farther* up every referrer's
resolution chain than the source, so once the source copy is deleted, every
reference that pointed at it falls through — by ordinary PAN-OS shadowing — onto
the destination definition. No repoint is ever needed.

That single safety property is why this engine is small. The genuinely tricky
situations are turned into blockers rather than clever rewrites:

- a sibling / child / unrelated destination would orphan references in the
  source's subtree → blocked;
- a device-group *between* source and destination that already defines the name
  would capture the fall-through instead of the destination → blocked
  (the intermediate-shadow guard);
- the moved object's own dependencies (group members, tags, service-group
  members, dynamic-filter tags) must already resolve from the destination, to
  the *same* object → otherwise blocked, *unless* `--cascade` is passed;
- the destination already defining the name is a *collision*: identical value →
  drop the source copy (the fall-through does the rest); different value →
  blocked.

The destination object on a clean promote is built by the matching
`crud.plan_*`, so the PAN-OS leaf-key contract and field validation are reused,
never re-derived here.

`--cascade` (issue #76) lifts the dependency blocker: instead of refusing, it
pulls the transitive downward dependency closure up to the *same* destination in
one ordered plan — deepest dependencies first (members/tags before the objects
that reference them), then the named object, then the source deletes. The
closure walk reuses dedup's cycle-safe resolution; each cascaded object still
runs the per-object gates (intermediate-shadow, collision). A dependency still
referenced by an object that *remains* in the source subtree is promoted but its
source copy is retained (with a warning), never deleted out from under a local
referrer.
"""

from __future__ import annotations

from psc.core import crud
from psc.core.changeset import ChangeSet, ObjectDelete, ObjectKind
from psc.core.models import (
    Address,
    AddressGroup,
    Location,
    Service,
    ServiceGroup,
    Snapshot,
    Tag,
)
from psc.core.normalize import normalize_address, service_key
from psc.core.refs import PREDEFINED, ReferenceGraph, Target, dag_filter_tags

# Each object kind resolves in exactly one namespace; address & address-group
# share `address`, service & service-group share `service` (mirrors `refs`).
_NAMESPACE: dict[ObjectKind, str] = {
    ObjectKind.ADDRESS: "address",
    ObjectKind.ADDRESS_GROUP: "address",
    ObjectKind.SERVICE: "service",
    ObjectKind.SERVICE_GROUP: "service",
    ObjectKind.TAG: "tag",
}

_Obj = Address | AddressGroup | Service | ServiceGroup | Tag


def _loc(name: str) -> Location:
    return Location.shared() if name == "shared" else Location.dg(name)


def _find(snapshot: Snapshot, kind: ObjectKind, name: str, loc: Location) -> _Obj | None:
    """The object of `kind` defined *directly* at `loc` (no inheritance), or None."""
    collections: dict[ObjectKind, list[_Obj]] = {
        ObjectKind.ADDRESS: list(snapshot.addresses),
        ObjectKind.ADDRESS_GROUP: list(snapshot.address_groups),
        ObjectKind.SERVICE: list(snapshot.services),
        ObjectKind.SERVICE_GROUP: list(snapshot.service_groups),
        ObjectKind.TAG: list(snapshot.tags),
    }
    return next((o for o in collections[kind] if o.name == name and o.location == loc), None)


def _same_value(kind: ObjectKind, a: _Obj, b: _Obj) -> bool:
    """Whether `a` and `b` carry the same match-affecting value.

    A collision at the destination only merges when the two objects mean the
    same thing — otherwise dropping the source silently changes rule matching.
    Comparison is per kind; a tag has no match-affecting value, so two tags of
    the same name always merge (a differing colour/comment is cosmetic).
    """
    if kind is ObjectKind.ADDRESS:
        assert isinstance(a, Address) and isinstance(b, Address)
        na, nb = normalize_address(a), normalize_address(b)
        if na is not None and nb is not None:
            return na.exact_key() == nb.exact_key()
        return a.type == b.type and a.value.strip() == b.value.strip()
    if kind is ObjectKind.SERVICE:
        assert isinstance(a, Service) and isinstance(b, Service)
        return service_key(a) == service_key(b)
    if kind is ObjectKind.ADDRESS_GROUP:
        assert isinstance(a, AddressGroup) and isinstance(b, AddressGroup)
        return (
            sorted(a.static_members or []) == sorted(b.static_members or [])
            and a.dynamic_filter == b.dynamic_filter
        )
    if kind is ObjectKind.SERVICE_GROUP:
        assert isinstance(a, ServiceGroup) and isinstance(b, ServiceGroup)
        return sorted(a.members) == sorted(b.members)
    return True  # tag: no value to compare


def _dependencies(kind: ObjectKind, obj: _Obj) -> list[tuple[str, str]]:
    """The `(namespace, name)` references the object itself carries downward.

    These must resolve from the destination after the move, since `psc` does not
    cascade them (v1). Tag references come from any object's `tags`; groups add
    their members, and a dynamic group adds the tags named in its filter.
    """
    deps: list[tuple[str, str]] = [("tag", t) for t in getattr(obj, "tags", [])]
    if isinstance(obj, AddressGroup):
        deps += [("address", m) for m in (obj.static_members or [])]
        if obj.dynamic_filter:
            deps += [("tag", t) for t in dag_filter_tags(obj.dynamic_filter)]
    elif isinstance(obj, ServiceGroup):
        deps += [("service", m) for m in obj.members]
    return deps


def _blocked(cs: ChangeSet) -> ChangeSet:
    """Enforce the repo invariant that a blocked plan carries zero ops."""
    cs.upserts.clear()
    cs.deletes.clear()
    cs.reference_edits.clear()
    return cs


def _promotion_blocker(
    snapshot: Snapshot,
    graph: ReferenceGraph,
    *,
    kind: ObjectKind,
    name: str,
    src_obj: _Obj,
    source: Location,
    dest: Location,
    namespace: str,
    check_dependencies: bool = True,
) -> str | None:
    """The first reason this promotion is unsafe, or None if every gate passes.

    Runs the direction gate, the intermediate-shadow guard, and (unless
    `check_dependencies` is False, i.e. `--cascade` will pull them up too) the
    dependency gate — see module docstring. Collision handling is the caller's
    job — it needs the destination object to decide merge-vs-block.
    """
    chain = snapshot.ancestors(source)  # [source, …ancestors…, shared]
    if dest == source:
        return f"source and destination are the same location ({source.name})"
    if dest not in chain:
        return (
            f"move only promotes toward shared: destination must be 'shared' or an "
            f"ancestor of {source.name} (a sibling/child destination would orphan "
            f"references in {source.name})"
        )

    # Nothing between source and dest may already define the name, or the
    # fall-through after deleting the source would resolve there, not at dest.
    for loc in chain[1 : chain.index(dest)]:
        if graph.defined_at(namespace, name, loc):
            return (
                f"device-group '{loc.name}' between {source.name} and {dest.name} already "
                f"defines '{name}' in the {namespace} namespace; promoting would re-resolve "
                "references to it — resolve that shadow first"
            )

    if not check_dependencies:
        return None

    # Every downward dependency must resolve from dest, to the *same* object it
    # resolves to from source (no auto-cascade in v1).
    broken: list[str] = []
    for ns, dep in _dependencies(kind, src_obj):
        if dep in PREDEFINED:
            continue
        src_t = graph.resolve(ns, dep, source)
        if src_t is None:
            continue  # already dangling at the source; the move neither helps nor harms
        dst_t = graph.resolve(ns, dep, dest)
        if dst_t is None:
            broken.append(f"'{dep}' is not visible at {dest.name}")
        elif dst_t != src_t:
            broken.append(
                f"'{dep}' resolves to a different object at {dest.name} ({dst_t.location.name})"
            )
    if broken:
        return (
            "dependencies do not resolve at the destination: "
            + "; ".join(broken)
            + (" — move them first")
        )
    return None


def _build_destination_upsert(
    snapshot: Snapshot, kind: ObjectKind, obj: _Obj, dest: Location
) -> ChangeSet:
    """Plan the create-at-destination via the matching `crud` planner (DRY)."""
    if isinstance(obj, Address):
        return crud.plan_address(
            snapshot,
            obj.name,
            obj.type,
            obj.value,
            description=obj.description,
            tags=obj.tags,
            location=dest,
        )
    if isinstance(obj, AddressGroup):
        return crud.plan_address_group(
            snapshot,
            obj.name,
            static_members=obj.static_members,
            dynamic_filter=obj.dynamic_filter,
            description=obj.description,
            tags=obj.tags,
            location=dest,
        )
    if isinstance(obj, Service):
        return crud.plan_service(
            snapshot,
            obj.name,
            obj.protocol,
            destination_port=obj.destination_port,
            source_port=obj.source_port,
            description=obj.description,
            tags=obj.tags,
            location=dest,
        )
    if isinstance(obj, ServiceGroup):
        return crud.plan_service_group(
            snapshot, obj.name, obj.members, tags=obj.tags, location=dest
        )
    return crud.plan_tag(snapshot, obj.name, color=obj.color, comments=obj.comments, location=dest)


# A cascade node's identity: (ObjectKind, name, source-location-name). Two
# objects of different kinds can share a name (address vs address-group), so the
# kind is part of the key.
_Node = tuple[ObjectKind, str, str]


def _obj_at(snapshot: Snapshot, target: Target) -> _Obj | None:
    """The concrete object a resolved `Target` names, or None if absent."""
    return _find(snapshot, ObjectKind(target.kind), target.name, target.location)


def _cascade_closure(
    snapshot: Snapshot,
    graph: ReferenceGraph,
    *,
    root_kind: ObjectKind,
    root_name: str,
    src_obj: _Obj,
    source: Location,
    dest: Location,
) -> list[tuple[ObjectKind, str, _Obj]]:
    """Deepest-first list of the objects `--cascade` must promote to `dest`.

    Post-order DFS over each object's downward dependencies (`_dependencies`),
    resolving every dependency *name* to its actual object along the source's
    device-group chain (PAN-OS shadowing). A dependency is pulled into the
    cascade only when it is **DG-local** to the source subtree — i.e. it resolves
    to an object defined *strictly below* `dest` (so it is not already visible
    there). Dependencies that already resolve at `dest` (or above it) are left
    untouched; the per-object gate later confirms they still resolve.

    Cycle-safe via `visiting`/`done`; the walk terminates on nested/cyclic groups
    the way dedup's closure does. The root object is emitted last (it references
    its dependencies, so it must be created after them).
    """
    # Locations strictly below dest but at/above source — the source subtree the
    # cascade is allowed to drain. A dep resolving here is DG-local; one resolving
    # at dest or an ancestor of dest is already visible and needs no promotion.
    chain = snapshot.ancestors(source)  # [source, …, dest, …, shared]
    local_locs = {loc.name for loc in chain[: chain.index(dest)]}

    ordered: list[tuple[ObjectKind, str, _Obj]] = []
    done: set[_Node] = set()
    visiting: set[_Node] = set()

    def visit(kind: ObjectKind, name: str, obj: _Obj, obj_loc: Location) -> None:
        node: _Node = (kind, name, obj_loc.name)
        if node in done or node in visiting:
            return  # already emitted, or an ancestor in this DFS branch (cycle)
        visiting.add(node)
        for ns, dep in _dependencies(kind, obj):
            if dep in PREDEFINED:
                continue
            src_t = graph.resolve(ns, dep, obj_loc)
            if src_t is None or src_t.location.name not in local_locs:
                continue  # dangling, or already visible at/above dest — not cascaded
            dep_obj = _obj_at(snapshot, src_t)
            if dep_obj is None:
                continue
            visit(ObjectKind(src_t.kind), src_t.name, dep_obj, src_t.location)
        visiting.discard(node)
        done.add(node)
        ordered.append((kind, name, obj))

    visit(root_kind, root_name, src_obj, source)
    return ordered


def _plan_cascade(
    cs: ChangeSet,
    snapshot: Snapshot,
    graph: ReferenceGraph,
    *,
    kind: ObjectKind,
    name: str,
    src_obj: _Obj,
    source: Location,
    dest: Location,
) -> None:
    """Fold the whole dependency closure's promotion into `cs`, in safe order.

    Every closure member runs the same per-object gates as a single move
    (intermediate-shadow, collision) via `_plan_one`; a blocker on any of them
    gates the entire cascade (the caller clears ops). Deepest dependencies are
    upserted first, then their parents, then the named object, then the source
    deletes — one inspectable, dependency-ordered plan.

    The retain-source rule: a cascaded dependency keeps its source copy when an
    object that *remains* in the source subtree still references it (a where-used
    check). Promoting it to `dest` keeps it visible to that referrer by
    inheritance, so leaving the source copy is safe — deleting it would strand
    the local referrer. Such a retention emits a warning, never a blocker.
    """
    closure = _cascade_closure(
        snapshot,
        graph,
        root_kind=kind,
        root_name=name,
        src_obj=src_obj,
        source=source,
        dest=dest,
    )
    # Identities being drained from the source (candidates for source deletion),
    # keyed by (target-kind, name, location) to match `where_used`/`resolve`.
    cascade_ids = {(k.value, n, o.location.name) for (k, n, o) in closure}

    # First pass: gate every member of the closure and collect ALL blockers, so
    # the operator sees the full list to fix in one go. The intermediate-shadow
    # guard still applies per dependency (direction is guaranteed — every object
    # shares one destination); the dependency gate is off because the closure
    # already accounts for downward deps. If anything is blocked we return before
    # planning a single op, so no misleading "will be removed" warnings leak from
    # deps that were never going to move.
    for obj_kind, obj_name, obj in closure:
        blocker = _promotion_blocker(
            snapshot,
            graph,
            kind=obj_kind,
            name=obj_name,
            src_obj=obj,
            source=obj.location,
            dest=dest,
            namespace=_NAMESPACE[obj_kind],
            check_dependencies=False,
        )
        if blocker is not None:
            cs.blockers.append(blocker)
    if cs.blockers:
        return

    # Second pass: emit the ordered plan (deepest deps first, root last).
    for obj_kind, obj_name, obj in closure:
        namespace = _NAMESPACE[obj_kind]
        obj_loc = obj.location
        # The named object always deletes its source copy — that *is* the move,
        # and its referrers fall through to the destination by shadowing. Only a
        # cascaded *dependency* is retain-eligible: it may still be needed by an
        # object staying behind in the source subtree.
        is_root = (obj_kind, obj_name, obj_loc.name) == (kind, name, source.name)
        retain = not is_root and _has_remaining_local_referrer(
            graph, kind=obj_kind, name=obj_name, loc=obj_loc, cascade_ids=cascade_ids
        )
        if retain:
            cs.warnings.append(
                f"{obj_kind.value} '{obj_name}'@{obj_loc.name} is still referenced by an object "
                f"remaining in {source.name}; it is promoted to {dest.name} but its "
                f"{obj_loc.name} copy is retained (delete it by hand once nothing local needs it)"
            )
        _plan_one(
            cs,
            snapshot,
            graph,
            kind=obj_kind,
            name=obj_name,
            src_obj=obj,
            source=obj_loc,
            dest=dest,
            namespace=namespace,
            delete_source=not retain,
        )


def _has_remaining_local_referrer(
    graph: ReferenceGraph,
    *,
    kind: ObjectKind,
    name: str,
    loc: Location,
    cascade_ids: set[tuple[str, str, str]],
) -> bool:
    """Whether an object outside the cascade set still references `name`@`loc`.

    `where_used` keys on the resolved target's kind, which for a group is
    `address-group`/`service-group`; a referrer whose own identity is itself in
    `cascade_ids` is being drained too, so it does not count. Any surviving
    referrer means the source copy must be retained.
    """
    for ref in graph.where_used(kind.value, name, loc):
        referrer_id = (ref.referrer_kind, ref.referrer_name, ref.referrer_location.name)
        if referrer_id not in cascade_ids:
            return True
    return False


def _revive_warnings(
    cs: ChangeSet,
    snapshot: Snapshot,
    graph: ReferenceGraph,
    *,
    name: str,
    namespace: str,
    dest: Location,
) -> None:
    """Warn when promoting `name` newly resolves a reference that dangles today.

    Once `name` is defined at `dest`, every location that inherits `dest` sees
    it; a reference that dangled on `name` from such a location will silently
    start resolving. Surface that side effect.
    """
    for ref in graph.dangling():
        if (
            ref.target_name == name
            and ref.namespace == namespace
            and dest in snapshot.ancestors(ref.referrer_location)
        ):
            cs.warnings.append(
                f"{ref.referrer_kind} '{ref.referrer_name}'@{ref.referrer_location.name} "
                f"{ref.field} currently dangles on '{name}' and will resolve to it after this move"
            )


def _plan_one(
    cs: ChangeSet,
    snapshot: Snapshot,
    graph: ReferenceGraph,
    *,
    kind: ObjectKind,
    name: str,
    src_obj: _Obj,
    source: Location,
    dest: Location,
    namespace: str,
    delete_source: bool,
) -> None:
    """Fold one object's promotion (collision + upsert/delete + revive) into `cs`.

    The collision handling is shared by the single-object and cascade paths:
    an identical-valued copy already at `dest` merges by dropping the source; a
    different-valued one is a hard blocker. `delete_source` is False for a
    cascaded dependency that must be retained (a local referrer still needs its
    source definition) — it is still promoted, just not deleted.
    """
    dest_obj = _find(snapshot, kind, name, dest)
    if dest_obj is not None:
        if not _same_value(kind, src_obj, dest_obj):
            cs.blockers.append(
                f"destination {dest.name} already defines {kind.value} '{name}' with a "
                "different value; merge or rename one side first"
            )
            return
        if delete_source:
            cs.warnings.append(
                f"{dest.name} already defines {kind.value} '{name}' with an identical value; the "
                f"{source.name} copy will be removed and references will resolve to the destination"
            )
    else:
        upsert_cs = _build_destination_upsert(snapshot, kind, src_obj, dest)
        cs.upserts.extend(upsert_cs.upserts)
        cs.blockers.extend(upsert_cs.blockers)
        cs.warnings.extend(upsert_cs.warnings)

    if delete_source:
        cs.deletes.append(ObjectDelete(kind=kind, name=name, location=source.name))

    _revive_warnings(cs, snapshot, graph, name=name, namespace=namespace, dest=dest)


def plan_move(
    snapshot: Snapshot,
    graph: ReferenceGraph,
    *,
    kind: ObjectKind,
    name: str,
    source_name: str,
    dest_name: str,
    cascade: bool = False,
) -> ChangeSet:
    """Plan promoting `kind`/`name` from `source_name` toward `dest_name`.

    Returns a `ChangeSet`; any unsafe condition yields a blocked, zero-op plan
    (see module docstring). The caller (`cli/_plan.complete`) refuses to apply a
    blocked plan, exactly like every other mutating command. With `cascade`,
    the object's transitive downward dependency closure is promoted too, in
    dependency order (see `_plan_cascade`).
    """
    source, dest = _loc(source_name), _loc(dest_name)
    namespace = _NAMESPACE[kind]
    cs = ChangeSet(title=f"move {kind.value} '{name}' @{source.name} -> @{dest.name}")

    src_obj = _find(snapshot, kind, name, source)
    if src_obj is None:
        cs.blockers.append(f"{kind.value} '{name}' is not defined at {source.name}")
        return _blocked(cs)

    # The named object's own gates always run. Under `cascade` the dependency
    # gate is skipped here — the closure walk pulls those deps up instead.
    blocker = _promotion_blocker(
        snapshot,
        graph,
        kind=kind,
        name=name,
        src_obj=src_obj,
        source=source,
        dest=dest,
        namespace=namespace,
        check_dependencies=not cascade,
    )
    if blocker is not None:
        cs.blockers.append(blocker)
        return _blocked(cs)

    if cascade:
        _plan_cascade(
            cs, snapshot, graph, kind=kind, name=name, src_obj=src_obj, source=source, dest=dest
        )
    else:
        _plan_one(
            cs,
            snapshot,
            graph,
            kind=kind,
            name=name,
            src_obj=src_obj,
            source=source,
            dest=dest,
            namespace=namespace,
            delete_source=True,
        )

    if cs.blockers:
        return _blocked(cs)
    return cs
