"""Find duplicate objects and plan their safe consolidation.

Duplicate detection groups objects by *meaning* (normalized value), not name,
so `h-web1`, `web-primary`, and `h-web1-slash` all collapse into one bucket.
Merging is the dangerous part: `plan_merge` repoints **every** reference to the
dropped object onto the kept one — across groups, security rules, and NAT —
*before* deleting it, and refuses (via `blockers`) when a reference can't be
safely repointed (e.g. the kept object isn't visible where the reference
lives). Differing values are a blocker unless explicitly allowed: merging two
objects that mean different things silently changes what rules match.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from psc.core.changeset import (
    ChangeSet,
    ObjectDelete,
    ObjectKind,
    ReferenceEdit,
    gate_unmappable_reference_edits,
)
from psc.core.models import Location, Snapshot
from psc.core.normalize import normalize_address, service_key
from psc.core.refs import Reference, ReferenceGraph
from psc.core.rulebases import rule_container

# Address-groups share the `address` namespace with address objects in the
# reference graph's `_addr_idx`; this is the namespace string every resolve/
# where-used call for a group must use (`"address-group"` would never resolve).
ADDR_NS = "address"


class ObjectRef(BaseModel):
    name: str
    location: str

    @property
    def loc(self) -> Location:
        return Location.shared() if self.location == "shared" else Location.dg(self.location)


class DuplicateGroup(BaseModel):
    kind: str  # "address" | "service"
    value: str  # canonical, human-readable
    members: list[ObjectRef] = Field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.members)


def find_duplicate_addresses(snapshot: Snapshot, *, strict: bool = True) -> list[DuplicateGroup]:
    """Address objects sharing the same value, 2+ per bucket.

    `strict` (default) groups only byte-identical values, so a host written with
    a subnet mask (`10.1.1.50/24`) is *not* a duplicate of its network
    (`10.1.1.0/24`). `strict=False` groups by masked network — the looser,
    fringe behaviour that collapses host-with-mask onto the network.
    """
    buckets: dict[str, list[ObjectRef]] = {}
    display: dict[str, str] = {}
    for a in snapshot.addresses:
        nv = normalize_address(a)
        if nv is None:
            continue
        key = nv.exact_key() if strict else nv.overlaps_key()
        buckets.setdefault(key, []).append(ObjectRef(name=a.name, location=a.location.name))
        label = (nv.exact or nv.key) if strict else nv.key
        display.setdefault(key, f"{a.type.value} {label}")
    return _to_groups("address", buckets, display)


def find_duplicate_services(snapshot: Snapshot) -> list[DuplicateGroup]:
    # Service dedup is already exact (protocol + normalized port lists); there
    # is no host-bit-masking analogue, so no strict/loose distinction applies.
    buckets: dict[str, list[ObjectRef]] = {}
    display: dict[str, str] = {}
    for s in snapshot.services:
        key = service_key(s)
        buckets.setdefault(key, []).append(ObjectRef(name=s.name, location=s.location.name))
        display.setdefault(key, key)
    return _to_groups("service", buckets, display)


def _to_groups[K](
    kind: str, buckets: dict[K, list[ObjectRef]], display: dict[K, str]
) -> list[DuplicateGroup]:
    groups = [
        DuplicateGroup(
            kind=kind, value=display[k], members=sorted(refs, key=lambda r: (r.location, r.name))
        )
        for k, refs in buckets.items()
        if len(refs) > 1
    ]
    return sorted(groups, key=lambda g: g.value)


class GroupDedupResult(BaseModel):
    """The outcome of a group-level dedup audit.

    `buckets` are the equivalent-group sets (2+ members); the two `*_skipped`
    lists name groups deliberately excluded from comparison so the CLI can warn
    that the audit is *not* exhaustive — a dynamic group has a runtime-only set,
    and an unresolvable one (dangling/malformed member) has no knowable set.
    """

    buckets: list[DuplicateGroup] = Field(default_factory=list)
    dynamic_skipped: list[str] = Field(default_factory=list)
    unresolvable_skipped: list[str] = Field(default_factory=list)


def resolve_group_members(  # noqa: PLR0911 — each early return is a distinct unresolvable case
    snapshot: Snapshot,
    graph: ReferenceGraph,
    name: str,
    loc: Location,
    *,
    _seen: frozenset[tuple[str, str]] = frozenset(),
) -> frozenset[str] | None:
    """The set of canonical leaf-address value keys a static group expands to.

    Recursively expands nested address-groups, resolving every member *name* to
    its actual object along the device-group chain (PAN-OS shadowing). Returns
    `None` — never a narrower set — when the group can't be reduced to a known
    set of leaf addresses: a dynamic group (runtime-only), a dangling member, or
    a malformed leaf value. An unresolvable group must never be declared
    equivalent to anything, so callers treat `None` as "incomparable".
    """
    group = next((g for g in snapshot.address_groups if g.name == name and g.location == loc), None)
    if group is None or group.dynamic_filter is not None or group.static_members is None:
        # Missing (caller's bug) or dynamic: no comparable static leaf set.
        return None
    if (name, loc.name) in _seen:
        # Already expanding this group higher in the recursion — a cycle. Its
        # members are accounted for by the outer frame; contribute nothing more.
        return frozenset()
    seen = _seen | {(name, loc.name)}

    keys: set[str] = set()
    for member in group.static_members:
        target = graph.resolve(ADDR_NS, member, loc)
        if target is None:
            return None  # dangling member: the set is unknowable, not narrower
        if target.kind == "address-group":
            nested = resolve_group_members(
                snapshot, graph, target.name, target.location, _seen=seen
            )
            if nested is None:
                return None
            keys |= nested
            continue
        addr = next(
            (
                a
                for a in snapshot.addresses
                if a.name == target.name and a.location == target.location
            ),
            None,
        )
        if addr is None:
            return None
        nv = normalize_address(addr)
        if nv is None:
            return None  # malformed leaf: don't silently drop it
        keys.add(nv.exact_key())
    return frozenset(keys)


def _group_closure(
    snapshot: Snapshot,
    graph: ReferenceGraph,
    name: str,
    loc: Location,
    *,
    _seen: frozenset[tuple[str, str]] = frozenset(),
) -> set[tuple[str, str]]:
    """The transitive set of address-GROUPS contained by a static group.

    Walks group→member-groups, resolving each member name to its actual object
    along the device-group chain (PAN-OS shadowing) and recursing only into
    members whose resolved target is itself an `address-group`. Cycle-safe via
    `_seen`. The returned identities are `(name, location.name)` of every nested
    group; the starting group is *not* included.
    """
    group = next((g for g in snapshot.address_groups if g.name == name and g.location == loc), None)
    if group is None or group.static_members is None:
        return set()
    if (name, loc.name) in _seen:
        return set()
    seen = _seen | {(name, loc.name)}

    out: set[tuple[str, str]] = set()
    for member in group.static_members:
        target = graph.resolve(ADDR_NS, member, loc)
        if target is None or target.kind != "address-group":
            continue
        out.add((target.name, target.location.name))
        out |= _group_closure(snapshot, graph, target.name, target.location, _seen=seen)
    return out


def find_duplicate_groups(
    snapshot: Snapshot, graph: ReferenceGraph, location: Location | None = None
) -> GroupDedupResult:
    """Bucket static address-groups by their effective leaf-address set.

    A bucket is emitted only when 2+ groups resolve to the *same* set. Dynamic
    and unresolvable groups are excluded and reported separately. With
    `location`, only groups at that location are compared (cross-location
    bucketing is otherwise allowed — same set, different DG, is still a redundant
    pair worth flagging).
    """
    buckets: dict[frozenset[str], list[ObjectRef]] = {}
    display: dict[frozenset[str], str] = {}
    dynamic_skipped: list[str] = []
    unresolvable_skipped: list[str] = []
    for g in snapshot.address_groups:
        if location is not None and g.location != location:
            continue
        if g.dynamic_filter is not None:
            dynamic_skipped.append(g.name)
            continue
        members = resolve_group_members(snapshot, graph, g.name, g.location)
        if members is None:
            unresolvable_skipped.append(g.name)
            continue
        buckets.setdefault(members, []).append(ObjectRef(name=g.name, location=g.location.name))
        display.setdefault(members, _group_set_label(members))
    return GroupDedupResult(
        buckets=_to_groups("address-group", buckets, display),
        dynamic_skipped=sorted(dynamic_skipped),
        unresolvable_skipped=sorted(unresolvable_skipped),
    )


def _group_set_label(members: frozenset[str]) -> str:
    """Human-readable, deterministic rendering of an effective member set."""
    if not members:
        return "{} (empty)"
    return "{" + ", ".join(sorted(members)) + "}"


def _rewrite_members(before: list[str], drop: str, keep: str) -> list[str]:
    """Replace `drop` with `keep`, de-duplicating while preserving order."""
    out: list[str] = []
    for m in before:
        repl = keep if m == drop else m
        if repl not in out:
            out.append(repl)
    return out


def _addr_value_key(snapshot: Snapshot, ref: ObjectRef) -> str | None:
    # Exact (host-bit-preserving) key: the merge gate must treat a /24-masked
    # host and the /24 network as *different* values, so it blocks the merge
    # unless --allow-value-change is passed.
    for a in snapshot.addresses:
        if a.name == ref.name and a.location == ref.loc:
            nv = normalize_address(a)
            return nv.exact_key() if nv else None
    return None


def plan_merge(
    snapshot: Snapshot,
    graph: ReferenceGraph,
    keep: ObjectRef,
    drop: ObjectRef,
    *,
    allow_value_change: bool = False,
) -> ChangeSet:
    """Plan collapsing the address `drop` into `keep` (repoint then delete)."""
    cs = ChangeSet(
        title=f"merge address '{drop.name}'@{drop.location} -> '{keep.name}'@{keep.location}"
    )

    keep_exists = any(a.name == keep.name and a.location == keep.loc for a in snapshot.addresses)
    drop_exists = any(a.name == drop.name and a.location == drop.loc for a in snapshot.addresses)
    if not keep_exists:
        cs.blockers.append(f"keep object '{keep.name}'@{keep.location} does not exist")
    if not drop_exists:
        cs.blockers.append(f"drop object '{drop.name}'@{drop.location} does not exist")
    if keep.name == drop.name and keep.location == drop.location:
        cs.blockers.append("keep and drop are the same object")
    if cs.is_blocked:
        return cs

    kv = _addr_value_key(snapshot, keep)
    dv = _addr_value_key(snapshot, drop)
    if kv is not None and dv is not None and kv != dv and not allow_value_change:
        cs.blockers.append(
            f"value mismatch: keep={kv} drop={dv}; merging would change rule matching "
            "(re-run with --allow-value-change to override)"
        )
        return cs

    refs = graph.where_used("address", drop.name, drop.loc)
    for ref in refs:
        # The kept name must resolve to the kept object in the referrer's scope.
        target = graph.resolve("address", keep.name, ref.referrer_location)
        if target is None or (target.name, target.location) != (keep.name, keep.loc):
            cs.blockers.append(
                f"cannot repoint {ref.referrer_kind} '{ref.referrer_name}'@"
                f"{ref.referrer_location.name} {ref.field}: kept object "
                f"'{keep.name}' is not visible there"
            )
            continue
        before = field_members(snapshot, ref)
        after = _rewrite_members(before, drop.name, keep.name)
        cs.reference_edits.append(
            ReferenceEdit(
                referrer_kind=ref.referrer_kind,
                referrer_name=ref.referrer_name,
                referrer_location=ref.referrer_location.name,
                field=ref.field,
                rulebase=ref.rulebase.value if ref.rulebase else None,
                before=before,
                after=after,
            )
        )
        if ref.field in ("source", "destination") and not after:
            cs.warnings.append(
                f"{ref.referrer_kind} '{ref.referrer_name}' {ref.field} would be emptied"
            )

    cs.deletes.append(ObjectDelete(kind=ObjectKind.ADDRESS, name=drop.name, location=drop.location))
    # Refuse any repoint the appliers would silently skip (e.g. a NAT translation
    # field) now that the delete is in the plan — a skipped repoint + delete is a
    # dangling reference. Mirrors `plan_rename`; offline and live share the gate.
    gate_unmappable_reference_edits(cs)

    if cs.blockers:
        # Invariant: a blocked plan carries zero ops, so no consumer can execute
        # a partial rewrite by iterating ops without checking `is_blocked`.
        cs.reference_edits.clear()
        cs.deletes.clear()
    return cs


def plan_merge_group(
    snapshot: Snapshot,
    graph: ReferenceGraph,
    keep: ObjectRef,
    drop: ObjectRef,
) -> ChangeSet:
    """Plan collapsing the address-GROUP `drop` into `keep` (repoint then delete).

    Mirrors `plan_merge` for groups: existence check, equivalence check (the two
    groups must expand to the *same* effective leaf-address set — there is no
    `--allow-value-change` escape hatch, because merging groups that mean
    different things silently changes rule matching), then repoint every
    referrer onto `keep` before deleting `drop`.
    """
    cs = ChangeSet(
        title=f"merge address-group '{drop.name}'@{drop.location} -> '{keep.name}'@{keep.location}"
    )

    def _exists(ref: ObjectRef) -> bool:
        return any(g.name == ref.name and g.location == ref.loc for g in snapshot.address_groups)

    if not _exists(keep):
        cs.blockers.append(f"keep object '{keep.name}'@{keep.location} does not exist")
    if not _exists(drop):
        cs.blockers.append(f"drop object '{drop.name}'@{drop.location} does not exist")
    if keep.name == drop.name and keep.location == drop.location:
        cs.blockers.append("keep and drop are the same object")
    if cs.is_blocked:
        return cs

    keep_set = resolve_group_members(snapshot, graph, keep.name, keep.loc)
    drop_set = resolve_group_members(snapshot, graph, drop.name, drop.loc)
    if keep_set is None or drop_set is None:
        cs.blockers.append(
            "has unresolvable members — fix dangling refs first "
            f"(keep={keep.name} resolvable={keep_set is not None}, "
            f"drop={drop.name} resolvable={drop_set is not None})"
        )
        return cs
    if keep_set != drop_set:
        cs.blockers.append(
            f"effective member sets differ: keep={_group_set_label(keep_set)} "
            f"drop={_group_set_label(drop_set)}; these groups are not equivalent"
        )
        return cs

    # Nested groups: if one of the pair contains the other (directly or
    # transitively), repointing drop->keep would make the kept group reference
    # itself or form a cycle, which PAN-OS rejects. Block rather than corrupt.
    keep_closure = _group_closure(snapshot, graph, keep.name, keep.loc)
    drop_closure = _group_closure(snapshot, graph, drop.name, drop.loc)
    if (drop.name, drop.loc.name) in keep_closure or (keep.name, keep.loc.name) in drop_closure:
        cs.blockers.append(
            f"cannot merge: '{keep.name}'@{keep.location} and '{drop.name}'@{drop.location} are "
            "nested (one contains the other); merging would create a self-referential or cyclic "
            "group — restructure manually"
        )
        return cs

    # `where_used` keys by the resolved Target KIND — which for a group is
    # "address-group" (see refs.py `_by_target`), so that is the correct lookup
    # key here. The visibility `resolve()` below, by contrast, takes a NAMESPACE,
    # which for both addresses and groups is "address" (ADDR_NS).
    refs = graph.where_used("address-group", drop.name, drop.loc)
    for ref in refs:
        # Defense-in-depth: the keep group must never be repointed into a member
        # of itself. The containment block above should already prevent reaching
        # here, but guard so a self-member edit can never be emitted.
        if ref.referrer_kind == "address-group" and (
            ref.referrer_name,
            ref.referrer_location,
        ) == (keep.name, keep.loc):
            continue
        target = graph.resolve(ADDR_NS, keep.name, ref.referrer_location)
        if target is None or (target.name, target.location) != (keep.name, keep.loc):
            cs.blockers.append(
                f"cannot repoint {ref.referrer_kind} '{ref.referrer_name}'@"
                f"{ref.referrer_location.name} {ref.field}: kept object "
                f"'{keep.name}' is not visible there"
            )
            continue
        before = field_members(snapshot, ref)
        after = _rewrite_members(before, drop.name, keep.name)
        cs.reference_edits.append(
            ReferenceEdit(
                referrer_kind=ref.referrer_kind,
                referrer_name=ref.referrer_name,
                referrer_location=ref.referrer_location.name,
                field=ref.field,
                rulebase=ref.rulebase.value if ref.rulebase else None,
                before=before,
                after=after,
            )
        )
        if ref.field in ("source", "destination") and not after:
            cs.warnings.append(
                f"{ref.referrer_kind} '{ref.referrer_name}' {ref.field} would be emptied"
            )

    cs.deletes.append(
        ObjectDelete(kind=ObjectKind.ADDRESS_GROUP, name=drop.name, location=drop.location)
    )
    gate_unmappable_reference_edits(cs)

    if cs.blockers:
        # Invariant: a blocked plan carries zero ops (mirror of plan_merge).
        cs.reference_edits.clear()
        cs.deletes.clear()
    return cs


def _field_attr(field: str) -> str:
    """The model attribute holding a reference field's member list.

    The reference `field` is the PAN-OS *element* name (`tag`, `source`,
    `destination-translation`); the model attribute is its Python form. The one
    irregular case is `tag` → `tags` — getting this wrong returns an empty list,
    so a tag rename/merge would wipe the field instead of rewriting one member.
    """
    return "tags" if field == "tag" else field.replace("-", "_")


def attr_as_members(obj: object, field: str) -> list[str]:
    """Read a rule's reference field as a member list (a scalar wraps to one)."""
    val = getattr(obj, _field_attr(field), [])
    return list(val) if isinstance(val, list) else [val]


# Back-compat alias for the prior private name.
_attr_as_members = attr_as_members


def field_members(snapshot: Snapshot, ref: Reference) -> list[str]:
    """Current member list of the field a reference points at."""
    loc = ref.referrer_location
    if ref.referrer_kind == "address-group":
        for ag in snapshot.address_groups:
            if ag.name == ref.referrer_name and ag.location == loc:
                return list(ag.static_members or [])
    elif ref.referrer_kind == "security-rule":
        for r in snapshot.security_rules:
            if (
                r.name == ref.referrer_name
                and r.location == loc
                and (ref.rulebase is None or r.rulebase == ref.rulebase)
            ):
                return attr_as_members(r, ref.field)
    elif ref.referrer_kind == "nat-rule":
        for n in snapshot.nat_rules:
            if (
                n.name == ref.referrer_name
                and n.location == loc
                and (ref.rulebase is None or n.rulebase == ref.rulebase)
            ):
                return attr_as_members(n, ref.field)
    elif rule_container(ref.referrer_kind) is not None:
        for p in snapshot.policy_rules:
            if (
                p.referrer_kind == ref.referrer_kind
                and p.name == ref.referrer_name
                and p.location == loc
                and (ref.rulebase is None or p.rulebase == ref.rulebase)
            ):
                return attr_as_members(p, ref.field)
    return [ref.target_name]
