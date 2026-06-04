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


def _to_groups(
    kind: str, buckets: dict[str, list[ObjectRef]], display: dict[str, str]
) -> list[DuplicateGroup]:
    groups = [
        DuplicateGroup(
            kind=kind, value=display[k], members=sorted(refs, key=lambda r: (r.location, r.name))
        )
        for k, refs in buckets.items()
        if len(refs) > 1
    ]
    return sorted(groups, key=lambda g: g.value)


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


def _field_attr(field: str) -> str:
    """The model attribute holding a reference field's member list.

    The reference `field` is the PAN-OS *element* name (`tag`, `source`,
    `destination-translation`); the model attribute is its Python form. The one
    irregular case is `tag` → `tags` — getting this wrong returns an empty list,
    so a tag rename/merge would wipe the field instead of rewriting one member.
    """
    return "tags" if field == "tag" else field.replace("-", "_")


def _attr_as_members(obj: object, field: str) -> list[str]:
    """Read a rule's reference field as a member list (a scalar wraps to one)."""
    val = getattr(obj, _field_attr(field), [])
    return list(val) if isinstance(val, list) else [val]


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
                return _attr_as_members(r, ref.field)
    elif ref.referrer_kind == "nat-rule":
        for n in snapshot.nat_rules:
            if (
                n.name == ref.referrer_name
                and n.location == loc
                and (ref.rulebase is None or n.rulebase == ref.rulebase)
            ):
                return _attr_as_members(n, ref.field)
    elif rule_container(ref.referrer_kind) is not None:
        for p in snapshot.policy_rules:
            if (
                p.referrer_kind == ref.referrer_kind
                and p.name == ref.referrer_name
                and p.location == loc
                and (ref.rulebase is None or p.rulebase == ref.rulebase)
            ):
                return _attr_as_members(p, ref.field)
    return [ref.target_name]
