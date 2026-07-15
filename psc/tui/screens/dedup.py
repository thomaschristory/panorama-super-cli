"""Dedup spoke: collapse a duplicate bucket toward a survivor, or promote it (#154).

Interpretation of issue #85's "device-group drop-down": the duplicate set is the
selected addresses (or services) that share a value, and the KEEP `Select` lets
the user pick which member survives a merge. Because every option is labelled
`name@location`, that Select *is* the scope choice — choosing a survivor chooses
which device-group's object wins (the rest are repointed onto it and removed).
No separate DG filter is needed: the multi-selection already scopes the bucket,
and the survivor Select encodes location.

Issue #154 adds a second `Select`: the destination. It is the mode switch, not a
separate spoke — leaving it blank keeps today's in-place merge
(`plan_merge_bucket`, address-only); choosing a location instead promotes the
whole bucket there (`plan_promote`), which is the only path for a bucket
`plan_merge_bucket` cannot handle (e.g. services) and the only path that fixes a
duplicate with no copy in `shared` at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Select, Static

from psc.core.changeset import ChangeSet, ObjectKind
from psc.core.dedup import ObjectRef, plan_merge_bucket
from psc.core.normalize import service_key
from psc.core.promote import plan_promote
from psc.core.refs import ReferenceGraph
from psc.tui.session import WorkbenchSession
from psc.tui.state import SelectionItem
from psc.tui.widgets.review import ReviewPanel, can_apply

if TYPE_CHECKING:
    from psc.tui.app import WorkbenchApp

_MIN_DUP_COUNT = 2

# Kinds the spoke can bucket. `plan_merge_bucket` is address-only, so a service
# bucket can be promoted but not merged in place — the screen reflects that by
# refusing to offer the blank (merge) destination option (see `compose`).
_BUCKET_KINDS = frozenset({"address", "service"})
_MERGEABLE_KINDS = frozenset({ObjectKind.ADDRESS})


def selection_bucket(session: WorkbenchSession) -> tuple[ObjectKind, list[ObjectRef]] | None:
    """The first duplicate bucket in the selection, with the kind it is made of.

    The selection must be homogeneous: an address and a service are never one
    bucket, and guessing which the user meant is worse than refusing. Items no
    longer in the working snapshot (stale selection) are skipped; if no bucket
    reaches two live members, the result is None.
    """
    items = session.selected_of_kinds(set(_BUCKET_KINDS))
    kinds = {i.kind for i in items}
    if len(kinds) != 1:
        return None
    kind = ObjectKind(next(iter(kinds)))

    snap = session.working_snapshot
    keys: dict[tuple[str, str], str]
    if kind is ObjectKind.ADDRESS:
        keys = {(a.location.name, a.name): a.value for a in snap.addresses}
    else:
        keys = {(s.location.name, s.name): service_key(s) for s in snap.services}

    by_value: dict[str, list[SelectionItem]] = {}
    for item in items:
        key = keys.get((item.location, item.name))
        if key is None:
            continue  # stale selection item; not in the current snapshot
        by_value.setdefault(key, []).append(item)

    for _key, group in by_value.items():
        if len(group) >= _MIN_DUP_COUNT:
            refs = [ObjectRef(name=i.name, location=i.location) for i in group]
            return (kind, sorted(refs, key=lambda r: (r.location, r.name)))
    return None


def promote_destinations(session: WorkbenchSession, members: list[ObjectRef]) -> list[str]:
    """Locations the WHOLE bucket can be promoted to: shared, plus common ancestors.

    A destination must be `shared` or an ancestor of every member (promote's
    upward-only rule), so intersecting the members' ancestor chains means an
    unreachable destination can never be picked in the first place. `shared` is in
    every chain, so the intersection is never empty.
    """
    snap = session.working_snapshot
    common: set[str] | None = None
    for m in members:
        chain = {loc.name for loc in snap.ancestors(m.loc)}
        common = chain if common is None else (common & chain)
    names = common or {"shared"}
    return sorted(names, key=lambda n: (n != "shared", n))


def plan_selection_bucket(
    session: WorkbenchSession,
    *,
    keep: ObjectRef | None = None,
    dest_name: str | None = None,
) -> tuple[str, ChangeSet] | None:
    """First duplicate bucket in the selection -> (label, plan).

    `dest_name` is the mode switch. None -> collapse the bucket onto one of its
    own members (`plan_merge_bucket`, today's behaviour, address-only). A
    location -> promote the whole bucket there (`plan_promote`), the only option
    when the bucket has no in-place merge planner (e.g. services) or no member at
    a common ancestor of every other member.
    """
    found = selection_bucket(session)
    if found is None:
        return None
    kind, members = found
    snap = session.working_snapshot
    graph = ReferenceGraph.build(snap)

    if dest_name is None:
        if kind not in _MERGEABLE_KINDS:
            return None  # no in-place merge planner for this kind
        cs = plan_merge_bucket(snap, graph, members=members, keep=keep)
        # `members` is already sorted by (location, name), so members[0] is the
        # same default survivor plan_merge_bucket would pick absent --keep; this
        # is only the *displayed* default, plan_merge_bucket's own ranking (which
        # additionally checks referrer-reachability) picks the real one.
        survivor = keep or members[0]
        return (f"merge {len(members) - 1} dup(s) -> {survivor.name}@{survivor.location}", cs)

    cs = plan_promote(snap, graph, kind=kind, members=members, dest_name=dest_name)
    return (f"promote {len(members)} {kind.value}(s) -> @{dest_name}", cs)


class DedupScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("ctrl+y", "stage", "stage"),
        ("escape", "app.pop_screen", "cancel"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        self._found = selection_bucket(session)
        self._kind: ObjectKind | None = self._found[0] if self._found is not None else None
        self._members: list[ObjectRef] = self._found[1] if self._found is not None else []

    def compose(self) -> ComposeResult:
        if self._found is None:
            yield Static("No duplicate bucket in the selection.", id="dedup-empty")
        else:
            # KEEP survivor picker: the option value is the member index; the label
            # is name@location, so the dropdown doubles as the scope choice (#85).
            # Only meaningful in merge mode (blank destination) — a promote plan
            # ignores it, since the promoted object gets one name regardless of
            # which member's copy was "kept" in the selection.
            yield Static("Choose the survivor (the rest are repointed + removed):")
            yield Select(
                [(f"{m.name}@{m.location}", idx) for idx, m in enumerate(self._members)],
                value=0,
                allow_blank=False,
                id="dedup-keep",
            )
            yield Static("Promote to (leave blank to merge in place):")
            yield Select(
                [(d, d) for d in promote_destinations(self.session, self._members)],
                # A non-mergeable kind (e.g. service) has no in-place plan at all,
                # so blank must not be offered — the honest UI for "this bucket
                # can only be promoted".
                allow_blank=self._kind in _MERGEABLE_KINDS,
                prompt="— merge in place —",
                id="dedup-dest",
            )
            yield ReviewPanel(id="review")
        yield Footer()

    def on_mount(self) -> None:
        if self._found is not None:
            self._render_plan()

    def _chosen_keep(self) -> ObjectRef | None:
        if not self._members:
            return None
        value = self.query_one("#dedup-keep", Select).value
        idx = value if isinstance(value, int) else 0
        return self._members[idx] if 0 <= idx < len(self._members) else None

    def _chosen_dest(self) -> str | None:
        if self._found is None:
            return None
        # NOTE: `Select.BLANK` is NOT the "no selection" sentinel in this
        # Textual version (it resolves to plain `False`, so `value is
        # Select.BLANK` never matches and a blank Select would be misread as
        # destination "Select.NULL" — see `#create-color`'s `sel()` in
        # create.py for the same pattern). `.is_blank()` is the real check.
        select = self.query_one("#dedup-dest", Select)
        return None if select.is_blank() else str(select.value)

    def _render_plan(self) -> None:
        plan = plan_selection_bucket(
            self.session, keep=self._chosen_keep(), dest_name=self._chosen_dest()
        )
        if plan is not None:
            self.query_one("#review", ReviewPanel).show(plan[1])

    def on_select_changed(self, event: Select.Changed) -> None:
        # Re-render the plan when either the survivor or the destination choice
        # changes, so the review panel reflects the currently-selected mode.
        if event.select.id in ("dedup-keep", "dedup-dest") and self._found is not None:
            self._render_plan()

    def action_stage(self) -> None:
        # Re-plan against the CURRENT snapshot rather than trusting the plan built
        # at screen-open time, and never let an engine/apply error crash the app.
        try:
            plan = plan_selection_bucket(
                self.session, keep=self._chosen_keep(), dest_name=self._chosen_dest()
            )
            if plan is None or not can_apply(plan[1]):
                self.app.bell()
                return
            label, cs = plan
            self.session.stage(label, cs)
        except Exception:
            self.app.bell()
            return
        # Refresh the hub view while it is still on the stack, then pop.
        cast("WorkbenchApp", self.app)._refresh_selection_view()
        self.app.pop_screen()
