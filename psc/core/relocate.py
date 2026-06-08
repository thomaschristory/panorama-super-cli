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
  the *same* object → otherwise blocked (no auto-cascade in v1);
- the destination already defining the name is a *collision*: identical value →
  drop the source copy (the fall-through does the rest); different value →
  blocked.

The destination object on a clean promote is built by the matching
`crud.plan_*`, so the PAN-OS leaf-key contract and field validation are reused,
never re-derived here.
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
from psc.core.refs import PREDEFINED, ReferenceGraph, dag_filter_tags

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
) -> str | None:
    """The first reason this promotion is unsafe, or None if every gate passes.

    Runs the direction gate, the intermediate-shadow guard, and the dependency
    gate (see module docstring). Collision handling is the caller's job — it
    needs the destination object to decide merge-vs-block.
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


def plan_move(
    snapshot: Snapshot,
    graph: ReferenceGraph,
    *,
    kind: ObjectKind,
    name: str,
    source_name: str,
    dest_name: str,
) -> ChangeSet:
    """Plan promoting `kind`/`name` from `source_name` toward `dest_name`.

    Returns a `ChangeSet`; any unsafe condition yields a blocked, zero-op plan
    (see module docstring). The caller (`cli/_plan.complete`) refuses to apply a
    blocked plan, exactly like every other mutating command.
    """
    source, dest = _loc(source_name), _loc(dest_name)
    namespace = _NAMESPACE[kind]
    cs = ChangeSet(title=f"move {kind.value} '{name}' @{source.name} -> @{dest.name}")

    src_obj = _find(snapshot, kind, name, source)
    if src_obj is None:
        cs.blockers.append(f"{kind.value} '{name}' is not defined at {source.name}")
        return _blocked(cs)

    blocker = _promotion_blocker(
        snapshot,
        graph,
        kind=kind,
        name=name,
        src_obj=src_obj,
        source=source,
        dest=dest,
        namespace=namespace,
    )
    if blocker is not None:
        cs.blockers.append(blocker)
        return _blocked(cs)

    # -- collision dispatch -------------------------------------------------
    dest_obj = _find(snapshot, kind, name, dest)
    if dest_obj is not None:
        if not _same_value(kind, src_obj, dest_obj):
            cs.blockers.append(
                f"destination {dest.name} already defines {kind.value} '{name}' with a "
                "different value; merge or rename one side first"
            )
            return _blocked(cs)
        cs.warnings.append(
            f"{dest.name} already defines {kind.value} '{name}' with an identical value; the "
            f"{source.name} copy will be removed and references will resolve to the destination"
        )
    else:
        upsert_cs = _build_destination_upsert(snapshot, kind, src_obj, dest)
        cs.upserts.extend(upsert_cs.upserts)
        cs.blockers.extend(upsert_cs.blockers)
        cs.warnings.extend(upsert_cs.warnings)

    cs.deletes.append(ObjectDelete(kind=kind, name=name, location=source.name))

    # -- side-effect warning: a dangling reference elsewhere that the promotion
    # newly makes resolve (the destination becomes visible to its location).
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

    if cs.blockers:
        return _blocked(cs)
    return cs
