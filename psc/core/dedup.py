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

from psc.core.changeset import ChangeSet, ObjectDelete, ObjectKind, ReferenceEdit
from psc.core.models import Location, Snapshot
from psc.core.normalize import normalize_address, service_key
from psc.core.refs import Reference, ReferenceGraph


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


def find_duplicate_addresses(snapshot: Snapshot) -> list[DuplicateGroup]:
    """Address objects sharing an identical normalized value, 2+ per bucket."""
    buckets: dict[str, list[ObjectRef]] = {}
    display: dict[str, str] = {}
    for a in snapshot.addresses:
        nv = normalize_address(a)
        if nv is None:
            continue
        key = nv.overlaps_key()
        buckets.setdefault(key, []).append(ObjectRef(name=a.name, location=a.location.name))
        display.setdefault(key, f"{a.type.value} {nv.key}")
    return _to_groups("address", buckets, display)


def find_duplicate_services(snapshot: Snapshot) -> list[DuplicateGroup]:
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
    for a in snapshot.addresses:
        if a.name == ref.name and a.location == ref.loc:
            nv = normalize_address(a)
            return nv.overlaps_key() if nv else None
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
        if ref.field in ("source-translation", "destination-translation"):
            # psc cannot safely rewrite NAT translation fields offline (the XML
            # path is nested and renderer-flagged), so deleting `drop` would
            # leave a dangling translation reference. Refuse rather than warn.
            cs.blockers.append(
                f"NAT rule '{ref.referrer_name}' references '{drop.name}' in "
                f"{ref.field}; psc can't safely rewrite NAT translation offline — "
                "edit that field manually, then re-run the merge"
            )

    if cs.blockers:
        # Invariant: a blocked plan carries zero ops, so no consumer can execute
        # a partial rewrite by iterating ops without checking `is_blocked`.
        cs.reference_edits.clear()
        return cs

    cs.deletes.append(ObjectDelete(kind=ObjectKind.ADDRESS, name=drop.name, location=drop.location))
    return cs


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
                return list(getattr(r, ref.field.replace("-", "_"), []))
    elif ref.referrer_kind == "nat-rule":
        for n in snapshot.nat_rules:
            if (
                n.name == ref.referrer_name
                and n.location == loc
                and (ref.rulebase is None or n.rulebase == ref.rulebase)
            ):
                attr = ref.field.replace("-", "_")
                val = getattr(n, attr, [])
                return list(val) if isinstance(val, list) else [val]
    return [ref.target_name]
