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

from pydantic import BaseModel

from psc.core.changeset import (
    ChangeSet,
    ObjectDelete,
    ObjectKind,
    ReferenceEdit,
    gate_unmappable_reference_edits,
)
from psc.core.dedup import (
    DuplicateGroup,
    ObjectRef,
    find_duplicate_addresses,
    find_duplicate_groups,
    find_duplicate_services,
    find_duplicate_tags,
    plan_repoints,
    resolve_group_members,
    rewrite_members,
    select_address_bucket,
    select_group_bucket,
    select_service_bucket,
    select_tag_bucket,
)
from psc.core.models import Address, AddressGroup, Location, Service, Snapshot, Tag
from psc.core.refs import ReferenceGraph
from psc.core.relocate import (
    NAMESPACE,
    Obj,
    build_destination_upsert,
    cascade_closure,
    find_object,
    has_remaining_local_referrer,
    promotion_blocker,
    revive_warnings,
    same_value,
    to_location,
)
from psc.output.errors import ErrorType, PscError

# The kinds `dedup` buckets. Addresses/services/groups bucket by value; tags
# bucket by NAME (a tag has no match-affecting value, but it CAN be redundantly
# defined in several locations — #162). Service-groups have no dedup finder, so
# they have no bucket to promote.
PROMOTABLE_KINDS = frozenset(
    {ObjectKind.ADDRESS, ObjectKind.SERVICE, ObjectKind.ADDRESS_GROUP, ObjectKind.TAG}
)

# The concrete object types a promotable bucket can hold. Narrower than
# relocate's `Obj` (no ServiceGroup). A Tag lacks `.tags`/`.description`, so
# `_drift_warnings` branches on type rather than reading them blindly.
PromotableObj = Address | AddressGroup | Service | Tag

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
        assert isinstance(obj, Address | AddressGroup | Service | Tag)
        out.append((m, obj))
    return out


def _drift_warnings(cs: ChangeSet, template: _Member, objs: list[_Member]) -> None:
    """Attributes a discarded copy carries that the promoted object will not.

    For the value-carrying kinds, tags are the sharp edge: they decide dynamic
    address-group membership, so a tag only on a discarded copy silently changes
    what a DAG matches. For a Tag object itself the drift is cosmetic —
    colour/comments — but still worth surfacing, since the survivor's appearance
    wins. Warn either way; the operator, not the tool, decides.
    """
    tpl_ref, tpl = template
    for m, obj in objs:
        if (m.name, m.location) == (tpl_ref.name, tpl_ref.location):
            continue
        # Template and members share a kind, so branching on the template narrows
        # both sides for mypy (a bucket of tags has a Tag template, and vice versa).
        if isinstance(tpl, Tag):
            assert isinstance(obj, Tag)
            if obj.color and obj.color != tpl.color:
                cs.warnings.append(
                    f"'{m.name}'@{m.location} has color {obj.color} the promoted copy will "
                    f"not carry (survivor keeps {tpl.color or 'no color'})"
                )
            if obj.comments and obj.comments != tpl.comments:
                cs.warnings.append(
                    f"'{m.name}'@{m.location} has comments the promoted copy will not carry"
                )
            continue
        assert not isinstance(obj, Tag)
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
    for m, obj in objs:
        if (m.name, m.location) == (template_ref.name, template_ref.location):
            continue
        if not same_value(kind, tpl, obj):
            cs.blockers.append(
                f"'{m.name}'@{m.location} does not carry the same value as "
                f"'{template_ref.name}'@{template_ref.location}; this is not one bucket"
            )


def _gate_group_equivalence(
    cs: ChangeSet, snapshot: Snapshot, graph: ReferenceGraph, *, objs: list[_Member]
) -> None:
    """Every group in the bucket must expand to the SAME effective leaf-address set.

    Not `same_value`: that compares member *names*, and two device-groups' copies can
    name their members differently (or nest them) and still mean the same hosts — or
    name them identically and mean different hosts, because each name resolves up its
    own device-group chain. Only the resolved leaf set settles it. Dynamic groups have
    a runtime-only set and unresolvable ones have no knowable set; both are blockers,
    never a guess.
    """
    sets: dict[tuple[str, str], frozenset[str]] = {}
    for m, _obj in objs:
        leaves = resolve_group_members(snapshot, graph, m.name, m.loc)
        if leaves is None:
            cs.blockers.append(
                f"'{m.name}'@{m.location} has unresolvable members (dynamic, dangling, or "
                "malformed) — its effective set is unknowable, so it cannot be promoted"
            )
            continue
        sets[(m.name, m.location)] = leaves
    if cs.blockers:
        return
    if len(set(sets.values())) > 1:
        cs.blockers.append(
            "effective member sets differ across the bucket; these groups are not "
            "equivalent and promoting one over the others would change rule matching"
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
    check_dependencies: bool,
) -> None:
    """Run relocate's direction/intermediate-shadow/dependency gates per source.

    A member already sitting at the destination is not a source: there is
    nothing to promote and nothing to shadow-check. Under `--cascade` the
    dependency gate is off (`check_dependencies=False`): the closure pulls those
    dependencies up to the destination too, so refusing on them defeats the flag.
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
            check_dependencies=check_dependencies,
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


def _plan_cascade_bucket(  # noqa: PLR0912 — explicit fold/gate/upsert/delete-with-retain phases
    cs: ChangeSet,
    snapshot: Snapshot,
    graph: ReferenceGraph,
    *,
    kind: ObjectKind,
    survivor: str,
    dest: Location,
    objs: list[_Member],
) -> None:
    """Promote every member's dependency closure, folded into ONE set of upserts.

    This is the whole reason group promotion is hard. Two device-groups' copies of a
    group cascade the SAME leaf names to the SAME destination. Planning each closure
    independently would upsert `h-web1@shared` twice — last write silently winning —
    so the closures are folded on `(kind, destination-name)`: the first claim wins,
    an identical second claim is redundant, and a second claim carrying a DIFFERENT
    value is a blocker, because there is no correct single object to promote.

    De-duplicating the *upserts* must not de-duplicate the *deletes*: every source
    copy still has to go, including the ones whose upsert was folded away. Sources are
    therefore tracked separately, keyed by their own identity rather than their
    destination slot.
    """
    # (kind, destination-name) -> (object, the member whose closure claimed it)
    claimed: dict[tuple[ObjectKind, str], tuple[Obj, ObjectRef]] = {}
    upsert_order: list[tuple[ObjectKind, str, Obj]] = []
    sources: list[tuple[ObjectKind, Obj, Location]] = []
    seen_sources: set[tuple[str, str, str]] = set()

    for m, obj in objs:
        if m.loc == dest:
            continue  # already home
        closure = cascade_closure(
            snapshot,
            graph,
            root_kind=kind,
            root_name=m.name,
            src_obj=obj,
            source=m.loc,
            dest=dest,
        )
        for c_kind, c_name, c_obj in closure:
            # Only the bucket's own roots are renamed onto the survivor; a cascaded
            # dependency keeps its name, because the promoted group refers to it BY
            # that name.
            is_root = c_kind is kind and c_name == m.name and c_obj.location == m.loc
            dest_name = survivor if is_root else c_name

            src_id = (c_kind.value, c_obj.name, c_obj.location.name)
            if src_id not in seen_sources:
                seen_sources.add(src_id)
                sources.append((c_kind, c_obj, c_obj.location))

            key = (c_kind, dest_name)
            prior = claimed.get(key)
            if prior is None:
                claimed[key] = (c_obj, m)
                upsert_order.append((c_kind, dest_name, c_obj))
            elif not same_value(c_kind, prior[0], c_obj):
                cs.blockers.append(
                    f"cascade conflict: {c_kind.value} '{dest_name}' would be promoted to "
                    f"{dest.name} from both '{prior[1].name}'@{prior[1].location} and "
                    f"'{m.name}'@{m.location}, but those copies carry different values; "
                    "consolidate them first"
                )
    if cs.blockers:
        return

    # Gate every distinct object once. Direction is guaranteed (one destination for
    # all of them); the dependency gate is off because the closure already accounts
    # for downward deps.
    for c_kind, _dest_name, c_obj in upsert_order:
        blocker = promotion_blocker(
            snapshot,
            graph,
            kind=c_kind,
            name=c_obj.name,
            src_obj=c_obj,
            source=c_obj.location,
            dest=dest,
            namespace=NAMESPACE[c_kind],
            check_dependencies=False,
        )
        if blocker is not None:
            cs.blockers.append(f"'{c_obj.name}'@{c_obj.location.name}: {blocker}")
    if cs.blockers:
        return

    # Deepest dependencies first, roots last — `cascade_closure` is already post-order,
    # and folding preserves each closure's internal order.
    for c_kind, dest_name, c_obj in upsert_order:
        _plan_destination(cs, snapshot, kind=c_kind, template=c_obj, survivor=dest_name, dest=dest)
    if cs.blockers:
        return

    cascade_ids = {(k.value, o.name, loc.name) for k, o, loc in sources}
    root_ids = {(kind.value, m.name, m.location) for m, _ in objs}
    for c_kind, c_obj, src_loc in sources:
        # A root always loses its source copy — that IS the promotion. Only a cascaded
        # dependency is retain-eligible: an object staying behind in the source subtree
        # may still need its device-group definition.
        if (c_kind.value, c_obj.name, src_loc.name) not in root_ids and (
            has_remaining_local_referrer(
                graph, kind=c_kind, name=c_obj.name, loc=src_loc, cascade_ids=cascade_ids
            )
        ):
            cs.warnings.append(
                f"{c_kind.value} '{c_obj.name}'@{src_loc.name} is still referenced by an object "
                f"remaining in {src_loc.name}; it is promoted to {dest.name} but its "
                f"{src_loc.name} copy is retained (delete it by hand once nothing local needs it)"
            )
            continue
        cs.deletes.append(ObjectDelete(kind=c_kind, name=c_obj.name, location=src_loc.name))


def _synthetic(
    snapshot: Snapshot, kind: ObjectKind, template: Obj, survivor: str, dest: Location
) -> Snapshot:
    """`snapshot` plus the promoted object, already defined at `dest`.

    Never applied — this exists purely so name resolution can see the world as it
    will be *after* the plan lands. Without it, repointing a referrer onto the
    survivor resolves against a snapshot where the survivor does not exist yet, and
    `plan_repoints` correctly (but uselessly) refuses.
    """
    promoted = template.model_copy(update={"name": survivor, "location": dest})
    field = {
        ObjectKind.ADDRESS: "addresses",
        ObjectKind.SERVICE: "services",
        ObjectKind.ADDRESS_GROUP: "address_groups",
        ObjectKind.TAG: "tags",
    }[kind]
    existing = list(getattr(snapshot, field))
    return snapshot.model_copy(update={field: [*existing, promoted]})


def _plan_rename_repoints(
    cs: ChangeSet,
    snapshot: Snapshot,
    *,
    kind: ObjectKind,
    template: Obj,
    survivor: str,
    dest: Location,
    objs: list[_Member],
) -> None:
    """Repoint the odd-named copies' referrers onto the survivor name.

    Members that already carry the survivor name need nothing: their references
    re-resolve upward by shadowing once the device-group copy is deleted, and
    `plan_repoints` deliberately emits no edit for that no-op rewrite.

    Every doomed copy is passed in `ignoring`, exactly as `plan_merge_bucket` does:
    a sibling duplicate still standing between a referrer and the survivor is on its
    way out too, and must not be read as a blocking shadow.
    """
    renamed = [(m, o) for m, o in objs if m.name != survivor]
    if not renamed:
        return

    synthetic = _synthetic(snapshot, kind, template, survivor, dest)
    sgraph = ReferenceGraph.build(synthetic)
    keep = ObjectRef(name=survivor, location=dest.name)
    ignoring = frozenset((kind.value, m.name, m.location) for m, _ in objs)

    # Successive drops on ONE field must chain: the second rewrite must operate on
    # the first's result, or a shared referrer keeps a still-dropped member.
    edit_index: dict[tuple[str, str, str, str, str | None], ReferenceEdit] = {}
    for m, _obj in renamed:
        sub = ChangeSet(title="")
        plan_repoints(
            sub,
            synthetic,
            sgraph,
            kind=kind.value,
            keep=keep,
            drop=m,
            refs=sgraph.where_used(kind.value, m.name, m.loc),
            ignoring=ignoring,
        )
        cs.blockers.extend(sub.blockers)
        cs.warnings.extend(sub.warnings)
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
                prior.after = rewrite_members(prior.after, m.name, survivor)


def _pick_survivor(cs: ChangeSet, objs: list[_Member], keep_name: str | None) -> str | None:
    """The name the bucket unifies on, or `None` when it can't (blocker appended).

    Without `keep_name`, a same-named bucket keeps that name and a divergent one is
    blocked — promotion by shadowing alone cannot reconcile two names. `keep_name`
    is the explicit opt-in to unify divergent names on one member's; naming a
    non-member is an input error, not a plan blocker.
    """
    names = {m.name for m, _ in objs}
    if keep_name is not None:
        if keep_name not in names:
            listed = ", ".join(sorted(names))
            raise PscError(
                f"--keep '{keep_name}' is not a member name of this bucket ({listed})",
                ErrorType.INPUT,
            )
        return keep_name
    if len(names) > 1:
        listed = ", ".join(f"'{m.name}'@{m.location}" for m, _ in objs)
        cs.blockers.append(
            f"bucket names diverge ({listed}); pass --keep NAME to unify them on one name"
        )
        return None
    return next(iter(names))


def _plan_bucket_ops(
    cs: ChangeSet,
    snapshot: Snapshot,
    graph: ReferenceGraph,
    *,
    kind: ObjectKind,
    template: Obj,
    survivor: str,
    dest: Location,
    objs: list[_Member],
    cascade: bool,
) -> None:
    """Emit the survivor upsert(s) and the source deletes for the bucket.

    The two strategies sit behind one call so `plan_promote` need not branch on
    `cascade` itself: the flat path upserts the survivor once and deletes every
    non-destination copy; the cascade path folds each member's dependency closure
    (see `_plan_cascade_bucket`). Either may append blockers; the caller gates.
    """
    if cs.blockers:
        return  # an earlier gate already refused; nothing to plan
    if cascade:
        _plan_cascade_bucket(
            cs, snapshot, graph, kind=kind, survivor=survivor, dest=dest, objs=objs
        )
        return
    _plan_destination(cs, snapshot, kind=kind, template=template, survivor=survivor, dest=dest)
    if cs.blockers:
        return
    for m, _obj in objs:
        if m.loc == dest and m.name == survivor:
            continue  # this IS the destination object
        cs.deletes.append(ObjectDelete(kind=kind, name=m.name, location=m.location))


def plan_promote(
    snapshot: Snapshot,
    graph: ReferenceGraph,
    *,
    kind: ObjectKind,
    members: list[ObjectRef],
    dest_name: str = "shared",
    keep_name: str | None = None,
    cascade: bool = False,
) -> ChangeSet:
    """Plan promoting a whole duplicate bucket to `dest_name` (default `shared`).

    Returns a `ChangeSet`; any unsafe condition yields a blocked, zero-op plan. The
    bucket's members must all carry the same value. Same-named members promote with
    zero reference edits (upward shadowing does the work); divergently-named members
    need `keep_name` to unify them on one survivor, which repoints every referrer of
    the odd-named copies onto the survivor before deleting them. A member already
    sitting at the destination under the survivor name is adopted rather than deleted.

    With `cascade`, each member's downward dependency closure (members, nested
    groups, tags) is pulled up to `dest` too, folded across the bucket so one
    upsert serves a leaf that recurs in several members — provided those copies
    carry the same value; a same-named leaf with a divergent value is a blocker,
    never a last-write-wins upsert.
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

    survivor = _pick_survivor(cs, objs, keep_name)
    if survivor is None:
        return _blocked(cs)

    # `objs` is rank-ordered, so this is the survivor-named copy nearest to shared.
    survivor_objs = [(m, o) for m, o in objs if m.name == survivor]
    template_ref, template = survivor_objs[0]

    if kind is ObjectKind.ADDRESS_GROUP:
        _gate_group_equivalence(cs, snapshot, graph, objs=objs)
    else:
        _check_same_value(cs, kind, (template_ref, template), objs)
    if cs.blockers:
        return _blocked(cs)

    _check_per_member_gates(
        cs,
        snapshot,
        graph,
        kind=kind,
        namespace=namespace,
        dest=dest,
        objs=objs,
        check_dependencies=not cascade,
    )
    _plan_bucket_ops(
        cs,
        snapshot,
        graph,
        kind=kind,
        template=template,
        survivor=survivor,
        dest=dest,
        objs=objs,
        cascade=cascade,
    )
    if cs.blockers:
        return _blocked(cs)

    # Repoint after the deletes are staged: the unmappable-reference gate only
    # fires when the plan also tears something down, so a doomed shadow must
    # already be in `cs.deletes` when the gate runs.
    _plan_rename_repoints(
        cs, snapshot, kind=kind, template=template, survivor=survivor, dest=dest, objs=objs
    )
    gate_unmappable_reference_edits(cs)
    if cs.blockers:
        return _blocked(cs)

    _drift_warnings(cs, (template_ref, template), objs)
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


class SkippedBucket(BaseModel):
    """A bucket `--all` deliberately did not promote, and why.

    Skipping is not the same as blocking. A blocker is a hard gate on ONE plan; in
    a sweep, one unpromotable bucket must not veto every other. So `plan_promote_all`
    excludes the bucket from the aggregate plan and reports it here — loudly, because
    silent truncation would read as "covered everything" when it did not.
    """

    value: str
    reason: str


def buckets_for_kind(
    snapshot: Snapshot, graph: ReferenceGraph, kind: ObjectKind
) -> list[DuplicateGroup]:
    """Every duplicate bucket of `kind`, using dedup's finders."""
    if kind is ObjectKind.ADDRESS:
        return find_duplicate_addresses(snapshot)
    if kind is ObjectKind.SERVICE:
        return find_duplicate_services(snapshot)
    if kind is ObjectKind.ADDRESS_GROUP:
        # Leaf-set-keyed, unlike the name-keyed `--name` selector: a sweep should
        # find equivalent groups whatever they are called. Dynamic and unresolvable
        # groups are excluded by the finder itself.
        return find_duplicate_groups(snapshot, graph).buckets
    if kind is ObjectKind.TAG:
        return find_duplicate_tags(snapshot)
    raise PscError(f"promote --all does not support {kind.value}", ErrorType.INPUT)


def select_bucket(
    snapshot: Snapshot, graph: ReferenceGraph, *, kind: ObjectKind, value: str
) -> DuplicateGroup:
    """The one bucket a `--group <value>` selector names."""
    if kind is ObjectKind.ADDRESS:
        return select_address_bucket(snapshot, value)
    if kind is ObjectKind.SERVICE:
        return select_service_bucket(snapshot, value)
    if kind is ObjectKind.ADDRESS_GROUP:
        return select_group_bucket(snapshot, value)
    if kind is ObjectKind.TAG:
        return select_tag_bucket(snapshot, value)
    raise PscError(f"promote --group does not support {kind.value}", ErrorType.INPUT)


def plan_promote_all(
    snapshot: Snapshot,
    graph: ReferenceGraph,
    *,
    kind: ObjectKind,
    dest_name: str = "shared",
    cascade: bool = False,
) -> tuple[ChangeSet, list[SkippedBucket]]:
    """Promote every promotable bucket of `kind` in one plan.

    Buckets that cannot be promoted are excluded and returned as `SkippedBucket`s;
    the aggregate plan's `blockers` stays empty by construction, so one bad bucket
    cannot veto the rest (see `SkippedBucket`).
    """
    dest = to_location(dest_name)
    cs = ChangeSet(title=f"promote all duplicate {kind.value} buckets -> @{dest.name}")
    skipped: list[SkippedBucket] = []

    planned: list[tuple[DuplicateGroup, ChangeSet]] = []
    for bucket in buckets_for_kind(snapshot, graph, kind):
        plan = plan_promote(
            snapshot,
            graph,
            kind=kind,
            members=list(bucket.members),
            dest_name=dest_name,
            cascade=cascade,
        )
        if plan.is_blocked:
            skipped.append(SkippedBucket(value=bucket.value, reason="; ".join(plan.blockers)))
            continue
        planned.append((bucket, plan))

    colliding = _colliding_buckets(planned, dest=dest)
    seen: set[tuple[str, str, str]] = set()
    for bucket, plan in planned:
        if bucket.value in colliding:
            skipped.append(
                SkippedBucket(
                    value=bucket.value,
                    reason=(
                        f"name clash: promoting this bucket would define a name at {dest.name} "
                        "that another bucket defines with a different value; rename one side first"
                    ),
                )
            )
            continue
        # Identical upserts can legitimately recur across buckets once cascade is in
        # play (two groups sharing a leaf), so fold rather than duplicate.
        for u in plan.upserts:
            key = (u.kind.value, u.name, u.location)
            if key in seen:
                continue
            seen.add(key)
            cs.upserts.append(u)
        cs.reference_edits.extend(plan.reference_edits)
        cs.deletes.extend(plan.deletes)
        cs.warnings.extend(plan.warnings)

    if skipped:
        cs.warnings.append(
            f"skipped {len(skipped)} bucket(s) that cannot be promoted to {dest.name}; "
            "see the skipped report"
        )
    return cs, skipped


def _colliding_buckets(
    planned: list[tuple[DuplicateGroup, ChangeSet]], *, dest: Location
) -> set[str]:
    """Bucket values whose plans would define the same destination name differently.

    Unique to `--all`: two buckets can each be internally sound and still fight over
    a name at the destination. Same name + same value would have been ONE bucket, so
    a name clash across buckets is always a value clash — applying both would upsert
    the object twice, last write silently winning. Skip both sides; there is no
    correct single object to promote.
    """
    owner: dict[tuple[str, str, str], tuple[object, str]] = {}
    colliding: set[str] = set()
    for bucket, plan in planned:
        for u in plan.upserts:
            if u.location != dest.name:
                continue
            key = (u.kind.value, u.name, u.location)
            prior = owner.get(key)
            if prior is None:
                owner[key] = (u, bucket.value)
            elif prior[0] != u:
                colliding.add(bucket.value)
                colliding.add(prior[1])
    return colliding
