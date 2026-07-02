"""Dedup spoke: collapse a duplicate bucket toward a user-chosen survivor.

Interpretation of issue #85's "device-group drop-down": the duplicate set is the
selected addresses that share a value, and the KEEP `Select` lets the user pick
which member survives. Because every option is labelled `name@location`, that
Select *is* the scope choice — choosing a survivor chooses which device-group's
object wins (the rest are repointed onto it and removed). No separate DG filter
is needed: the multi-selection already scopes the bucket, and the survivor Select
encodes location.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Select, Static

from psc.core.changeset import ChangeSet
from psc.core.dedup import ObjectRef, plan_merge_bucket
from psc.core.refs import ReferenceGraph
from psc.tui.session import WorkbenchSession
from psc.tui.state import SelectionItem
from psc.tui.widgets.review import ReviewPanel, can_apply

if TYPE_CHECKING:
    from psc.tui.app import WorkbenchApp

_MIN_DUP_COUNT = 2


def selection_bucket(session: WorkbenchSession) -> list[ObjectRef] | None:
    """The first duplicate bucket in the selection, as ordered `ObjectRef`s.

    Groups selected addresses by their (current-snapshot) value and returns the
    first bucket of 2+ members, sorted deterministically (by location, name) so
    the KEEP dropdown order is stable. Items no longer in the working snapshot
    (stale selection) are silently skipped; if no bucket reaches two live
    members, the result is None.
    """
    addrs = session.selected_of_kinds({"address"})
    snap = session.working_snapshot
    index = {(a.location.name, a.name): a for a in snap.addresses}
    by_value: dict[str, list[SelectionItem]] = {}
    for item in addrs:
        obj = index.get((item.location, item.name))
        if obj is None:
            continue  # stale selection item; not in the current snapshot
        by_value.setdefault(obj.value, []).append(item)
    for _value, group in by_value.items():
        if len(group) >= _MIN_DUP_COUNT:
            refs = [ObjectRef(name=i.name, location=i.location) for i in group]
            return sorted(refs, key=lambda r: (r.location, r.name))
    return None


def plan_selection_bucket_merge(
    session: WorkbenchSession, keep: ObjectRef | None = None
) -> tuple[str, ChangeSet] | None:
    """First duplicate bucket in the selection -> (label, whole-bucket merge plan).

    Collapses the entire bucket toward `keep` (defaulting to the sorted-first
    member) in ONE ChangeSet via `plan_merge_bucket` — every non-survivor is
    repointed onto the survivor and deleted. Returns None when fewer than two
    live selected addresses share a value.
    """
    members = selection_bucket(session)
    if members is None:
        return None
    snap = session.working_snapshot
    graph = ReferenceGraph.build(snap)
    cs = plan_merge_bucket(snap, graph, members=members, keep=keep)
    survivor = keep or sorted(members, key=lambda r: (r.location, r.name))[0]
    n_drop = len(members) - 1
    label = f"merge {n_drop} dup(s) -> {survivor.name}@{survivor.location}"
    return (label, cs)


class DedupScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("ctrl+y", "stage", "stage merge"),
        ("escape", "app.pop_screen", "cancel"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        self._members = selection_bucket(session)

    def compose(self) -> ComposeResult:
        if self._members is None:
            yield Static("No duplicate addresses in the selection.", id="dedup-empty")
        else:
            # KEEP survivor picker: the option value is the member index; the label
            # is name@location, so the dropdown doubles as the scope choice (#85).
            yield Static("Choose the survivor (the rest are repointed + removed):")
            yield Select(
                [(f"{m.name}@{m.location}", idx) for idx, m in enumerate(self._members)],
                value=0,
                allow_blank=False,
                id="dedup-keep",
            )
            yield ReviewPanel(id="review")
        yield Footer()

    def on_mount(self) -> None:
        if self._members is not None:
            self._render_plan()

    def _chosen_keep(self) -> ObjectRef | None:
        if not self._members:
            return None
        value = self.query_one("#dedup-keep", Select).value
        idx = value if isinstance(value, int) else 0
        return self._members[idx] if 0 <= idx < len(self._members) else None

    def _render_plan(self) -> None:
        plan = plan_selection_bucket_merge(self.session, keep=self._chosen_keep())
        if plan is not None:
            self.query_one("#review", ReviewPanel).show(plan[1])

    def on_select_changed(self, event: Select.Changed) -> None:
        # Re-render the plan when the survivor choice changes so the review panel
        # reflects which objects get dropped.
        if event.select.id == "dedup-keep" and self._members is not None:
            self._render_plan()

    def action_stage(self) -> None:
        # Re-plan against the CURRENT snapshot rather than trusting the plan built
        # at screen-open time, and never let an engine/apply error crash the app.
        try:
            plan = plan_selection_bucket_merge(self.session, keep=self._chosen_keep())
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
