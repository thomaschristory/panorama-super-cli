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

from collections.abc import Sequence

from pydantic import BaseModel, Field

from psc.core.changeset import (
    ChangeSet,
    ObjectDelete,
    ObjectKind,
    ReferenceEdit,
    gate_unmappable_reference_edits,
)
from psc.core.dagfilter import FilterParseError, filter_tags
from psc.core.models import Address, Location, Snapshot
from psc.core.normalize import normalize_address, service_key
from psc.core.refs import Reference, ReferenceGraph
from psc.core.rulebases import rule_container
from psc.output.errors import ErrorType, PscError

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
    kind: str  # "address" | "service" | "address-group"
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


def _find_address(snapshot: Snapshot, ref: ObjectRef) -> Address | None:
    return next(
        (a for a in snapshot.addresses if a.name == ref.name and a.location == ref.loc), None
    )


def _post_merge_namespace(
    kind: str, drop: ObjectRef, also_dropping: Sequence[ObjectRef] | None
) -> frozenset[tuple[str, str, str]]:
    """The objects this plan deletes, as `(kind, name, location)` triples to hide
    from name resolution — see `ReferenceGraph.resolve(ignoring=...)`.

    `kind` distinguishes an address from an address-group: they share one
    namespace, so a slot may be occupied by the *other* kind, which this plan does
    not delete and which therefore keeps shadowing.
    """
    return frozenset((kind, r.name, r.location) for r in (drop, *(also_dropping or ())))


def _dags_matching_tag(snapshot: Snapshot, tag: str) -> list[str]:
    """Dynamic address-groups whose filter mentions `tag`. An unparseable filter
    is skipped rather than guessed at — `refs` already warns about those."""
    hits: list[str] = []
    for g in snapshot.address_groups:
        if not g.dynamic_filter:
            continue
        try:
            if tag in filter_tags(g.dynamic_filter):
                hits.append(f"'{g.name}'@{g.location.name}")
        except FilterParseError:
            continue
    return hits


def _attribute_drift_warnings(snapshot: Snapshot, keep: ObjectRef, drop: ObjectRef) -> list[str]:
    """Attributes the dropped object carries that the survivor does not.

    The merge gate compares *values* only, so a device-group shadow can differ in
    tags or description and still be a legitimate duplicate. Tags are the sharp
    edge: they decide dynamic address-group membership, so losing one changes what
    traffic a DAG matches. Warn — the operator, not the tool, decides.
    """
    keep_obj = _find_address(snapshot, keep)
    drop_obj = _find_address(snapshot, drop)
    if keep_obj is None or drop_obj is None:
        return []

    out: list[str] = []
    lost_tags = sorted(set(drop_obj.tags) - set(keep_obj.tags))
    if lost_tags:
        # No square brackets: warnings render through rich, which would swallow
        # `[prod, dmz]` as a markup tag and print an empty list.
        out.append(
            f"dropped '{drop.name}'@{drop.location} has tags not on "
            f"'{keep.name}'@{keep.location}: {', '.join(lost_tags)}"
        )
    for tag in lost_tags:
        for dag in _dags_matching_tag(snapshot, tag):
            out.append(
                f"tag '{tag}' is used by dynamic address-group {dag} — its membership will change"
            )
    if drop_obj.description and drop_obj.description != keep_obj.description:
        out.append(
            f"dropped '{drop.name}'@{drop.location} has a description that "
            f"'{keep.name}'@{keep.location} does not carry"
        )
    return out


def _reachable_by_referrers_of(
    snapshot: Snapshot, graph: ReferenceGraph, candidate: ObjectRef, members: list[ObjectRef]
) -> bool:
    """Can every reference to the *other* bucket members see `candidate`?

    A referrer resolves a bare name up its own container chain, so a survivor
    outside that chain can never be repointed to. This is the necessary half of
    the visibility gate in `_plan_repoints` (it cannot know about surviving
    shadows), used only to pick a default survivor that does not needlessly
    strand another member's rules.
    """
    for m in members:
        if (m.name, m.location) == (candidate.name, candidate.location):
            continue
        for ref in graph.where_used("address", m.name, m.loc):
            if candidate.loc not in snapshot.ancestors(ref.referrer_location):
                return False
    return True


def _clear_if_blocked(cs: ChangeSet) -> None:
    """A blocked plan carries zero ops — and makes no claims.

    The ops go so that no consumer can execute a partial rewrite by iterating them
    without checking `is_blocked`. The warnings go with them: they describe what
    applying *would* do, and this plan will never be applied (`complete()` puts
    them straight into the CONFLICT error envelope, where "N reference(s) will
    re-resolve" beside "plan blocked" reads as a contradiction). `blockers` is the
    whole message.
    """
    if not cs.blockers:
        return
    cs.reference_edits.clear()
    cs.deletes.clear()
    cs.warnings.clear()


def _plan_repoints(
    cs: ChangeSet,
    snapshot: Snapshot,
    graph: ReferenceGraph,
    *,
    kind: str,
    keep: ObjectRef,
    drop: ObjectRef,
    refs: list[Reference],
    ignoring: frozenset[tuple[str, str, str]],
) -> None:
    """Repoint every referrer of `drop` onto `keep`, or block where it can't be.

    Shared by the address and address-group planners — both resolve in the
    `address` namespace and rewrite flat member lists identically. `kind`
    (`address` / `address-group`) is what tells them apart *inside* that one
    namespace.
    """
    collapsing = 0
    for ref in refs:
        # The kept name must resolve to the kept object in the referrer's scope
        # *once this plan has applied* — the objects being deleted are exactly the
        # shadows that would otherwise stop the upward walk short of the survivor.
        # The kind is part of the identity: a same-named object of the *other*
        # kind at the keep's own slot is a different object, not the survivor.
        target = graph.resolve(ADDR_NS, keep.name, ref.referrer_location, ignoring=ignoring)
        if target is None or (target.kind, target.name, target.location) != (
            kind,
            keep.name,
            keep.loc,
        ):
            cs.blockers.append(
                f"cannot repoint {ref.referrer_kind} '{ref.referrer_name}'@"
                f"{ref.referrer_location.name} {ref.field}: kept object "
                f"'{keep.name}' is not visible there"
            )
            continue
        collapsing += 1
        before = field_members(snapshot, ref)
        after = _rewrite_members(before, drop.name, keep.name)
        if after == before:
            # Same-name collapse: the member list is untouched and the reference
            # simply re-resolves upward to the survivor. Emitting a no-op edit
            # would write the field back unchanged — and, worse, drag fields the
            # appliers cannot rewrite (a NAT translation, a PBF next-hop) into the
            # unmappable-reference gate, blocking a merge that needs no rewrite.
            continue
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

    if keep.name == drop.name and collapsing:
        # Only the references that cleared the gate re-resolve; a blocked one goes
        # nowhere (and takes the whole plan with it), so counting it here would
        # tell the operator the opposite of what happens.
        cs.warnings.append(
            f"{collapsing} reference(s) will re-resolve from '{drop.name}'@{drop.location} "
            f"to '{keep.name}'@{keep.location} (inheritance collapse)"
        )


def plan_merge(
    snapshot: Snapshot,
    graph: ReferenceGraph,
    keep: ObjectRef,
    drop: ObjectRef,
    *,
    allow_value_change: bool = False,
    also_dropping: Sequence[ObjectRef] | None = None,
) -> ChangeSet:
    """Plan collapsing the address `drop` into `keep` (repoint then delete).

    `also_dropping` names the other objects a *composite* plan deletes (see
    `plan_merge_bucket`). They are hidden from name resolution alongside `drop`,
    so a sibling duplicate that is itself on its way out cannot be mistaken for a
    shadow blocking the survivor.
    """
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

    _plan_repoints(
        cs,
        snapshot,
        graph,
        kind="address",
        keep=keep,
        drop=drop,
        refs=graph.where_used("address", drop.name, drop.loc),
        ignoring=_post_merge_namespace("address", drop, also_dropping),
    )
    cs.warnings.extend(_attribute_drift_warnings(snapshot, keep, drop))

    cs.deletes.append(ObjectDelete(kind=ObjectKind.ADDRESS, name=drop.name, location=drop.location))
    # Refuse any repoint the appliers would silently skip (e.g. a NAT translation
    # field) now that the delete is in the plan — a skipped repoint + delete is a
    # dangling reference. Mirrors `plan_rename`; offline and live share the gate.
    gate_unmappable_reference_edits(cs)

    _clear_if_blocked(cs)
    return cs


def select_address_bucket(snapshot: Snapshot, value: str, *, strict: bool = True) -> DuplicateGroup:
    """Find the duplicate-address bucket matching a user-supplied `--group` value.

    Accepts either the full display string `dedup addresses` prints
    (`ip-netmask 10.0.0.10/32`) or just the value token (`10.0.0.10/32`). Raises
    an INPUT error when nothing — or ambiguously more than one bucket — matches,
    so the CLI surfaces a clean usage error rather than a silent no-op.
    """
    wanted = value.strip()
    matches = [
        g
        for g in find_duplicate_addresses(snapshot, strict=strict)
        if g.value == wanted or g.value.split(" ", 1)[-1] == wanted
    ]
    if not matches:
        raise PscError(
            f"no duplicate-address bucket matches '{value}' "
            "(run `dedup addresses` to list buckets)",
            ErrorType.INPUT,
        )
    if len(matches) > 1:
        raise PscError(
            f"'{value}' matches {len(matches)} buckets; qualify it with the type prefix "
            "(e.g. 'ip-netmask 10.0.0.10/32')",
            ErrorType.INPUT,
        )
    return matches[0]


def select_service_bucket(snapshot: Snapshot, value: str) -> DuplicateGroup:
    """Find the duplicate-service bucket matching a user-supplied `--group` value.

    Simpler than the address case: `service_key` is already canonical and unique
    per bucket, so no type prefix and no ambiguity are possible.
    """
    wanted = value.strip()
    matches = [g for g in find_duplicate_services(snapshot) if g.value == wanted]
    if not matches:
        raise PscError(
            f"no duplicate-service bucket matches '{value}' (run `dedup services` to list buckets)",
            ErrorType.INPUT,
        )
    return matches[0]


def plan_merge_bucket(
    snapshot: Snapshot,
    graph: ReferenceGraph,
    *,
    members: list[ObjectRef],
    keep: ObjectRef | None = None,
    allow_value_change: bool = False,
) -> ChangeSet:
    """Collapse an entire duplicate bucket toward one survivor in a single plan.

    Composes the pairwise `plan_merge` for every dropped member against the same
    survivor and folds the results into one `ChangeSet`: repoints from all
    dropped objects onto `keep`, then deletes every dropped object. Blockers and
    warnings aggregate across members, and the same unmappable-reference gate
    applies — a single un-repointable reference gates the whole plan (zero ops).

    `keep` must be one of `members`; omitting it selects the *most visible* member
    — the one highest in the container hierarchy (`shared`, else the device-group
    nearest the root), ties broken by location then name, but skipping any member
    the other members' referrers could not reach (see `_reachable_by_referrers_of`).
    Collapsing a bucket upward is what makes the duplicates disappear for every
    device-group at once; keeping a leaf device-group's copy instead would leave
    the survivor invisible to its siblings. A `keep` outside the bucket is an INPUT
    error.
    """
    if not members:
        raise PscError("empty duplicate bucket", ErrorType.INPUT)

    def rank(m: ObjectRef) -> tuple[int, str, str]:
        # Depth from `shared`: ancestors() is [self, ...parents, shared], so
        # `shared` itself is depth 0 and sorts first.
        return (len(snapshot.ancestors(m.loc)) - 1, m.location, m.name)

    ordered = sorted(members, key=rank)
    if keep is None:
        # Height is not the whole story: the nearest-to-root member may sit in an
        # unrelated branch that the other members' rules can never resolve into,
        # which would block the whole bucket for a merge a lower-ranked member can
        # complete. Take the best-ranked member that every other member's referrers
        # can actually see, else fall back to the top rank so the operator is shown
        # the strongest candidate's blockers.
        keep = next(
            (m for m in ordered if _reachable_by_referrers_of(snapshot, graph, m, ordered)),
            ordered[0],
        )
    elif not any(m.name == keep.name and m.location == keep.location for m in ordered):
        names = ", ".join(f"'{m.name}'@{m.location}" for m in ordered)
        raise PscError(
            f"--keep '{keep.name}'@{keep.location} is not a member of this bucket ({names})",
            ErrorType.INPUT,
        )

    drops = [m for m in ordered if (m.name, m.location) != (keep.name, keep.location)]
    cs = ChangeSet(title=f"merge bucket ({len(drops)} object(s)) -> '{keep.name}'@{keep.location}")

    # Fold each pairwise plan's ops in. Reference edits touching the same field
    # must chain: the second drop rewrites the *first drop's* result, not the
    # original members, or a shared referrer would keep a still-dropped member.
    edit_index: dict[tuple[str, str, str, str, str | None], ReferenceEdit] = {}
    for drop in drops:
        # Every pairwise sub-plan must hide *all* the bucket's drops, not just its
        # own: a sibling duplicate still standing between a referrer and the
        # survivor is on its way out too, and must not be read as a blocking shadow.
        sub = plan_merge(
            snapshot,
            graph,
            keep=keep,
            drop=drop,
            allow_value_change=allow_value_change,
            also_dropping=drops,
        )
        cs.warnings.extend(sub.warnings)
        cs.blockers.extend(sub.blockers)
        for edit in sub.reference_edits:
            key = (
                edit.referrer_kind,
                edit.referrer_name,
                edit.referrer_location,
                edit.field,
                edit.rulebase,
            )
            prior = edit_index.get(key)
            if prior is None:
                edit_index[key] = edit
                cs.reference_edits.append(edit)
            else:
                # Re-derive the rewrite against the already-accumulated `after`,
                # so successive drops on one field compose instead of clobbering.
                prior.after = _rewrite_members(prior.after, drop.name, keep.name)
        cs.deletes.extend(sub.deletes)

    # Re-run the shared teardown gate over the *combined* plan (individual subs
    # already gated themselves, but recompute so aggregated ops are consistent).
    gate_unmappable_reference_edits(cs)

    _clear_if_blocked(cs)
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
    refs = [
        r
        for r in graph.where_used("address-group", drop.name, drop.loc)
        # Defense-in-depth: the keep group must never be repointed into a member
        # of itself. The containment block above should already prevent reaching
        # here, but guard so a self-member edit can never be emitted.
        if not (
            r.referrer_kind == "address-group"
            and (r.referrer_name, r.referrer_location) == (keep.name, keep.loc)
        )
    ]
    _plan_repoints(
        cs,
        snapshot,
        graph,
        kind="address-group",
        keep=keep,
        drop=drop,
        refs=refs,
        ignoring=_post_merge_namespace("address-group", drop, None),
    )

    cs.deletes.append(
        ObjectDelete(kind=ObjectKind.ADDRESS_GROUP, name=drop.name, location=drop.location)
    )
    gate_unmappable_reference_edits(cs)

    _clear_if_blocked(cs)
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
