"""Delete spoke: reference-safe removal of the selected objects.

This is the workbench face of `psc delete`. It plans one `plan_purge` over the
whole selection, so a group and its last member tear down together in one
`ChangeSet` instead of two plans that fight each other.

The spoke shows the plan before it stages anything. The engine warns about a
shared candidate and about a tagged candidate, because a dynamic address-group
can select a tagged address at runtime through an externally registered IP. The
review panel shows those warnings, so the operator reads them before the delete
reaches the changelist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Checkbox, Footer, Static

from psc.core.changeset import ChangeSet
from psc.core.models import Location
from psc.core.purge import PURGE_KINDS, plan_purge
from psc.core.refs import ReferenceGraph, Target
from psc.output.errors import PscError
from psc.tui.session import WorkbenchSession
from psc.tui.state import SelectionItem
from psc.tui.widgets.review import ReviewPanel, can_apply

if TYPE_CHECKING:
    from psc.tui.app import WorkbenchApp

_DELETABLE_KINDS = set(PURGE_KINDS)


def deletable_items(session: WorkbenchSession) -> list[SelectionItem]:
    """The selected objects a delete plan can take."""
    return session.selected_of_kinds(_DELETABLE_KINDS)


def _target(item: SelectionItem) -> Target:
    location = Location.shared() if item.location == "shared" else Location.dg(item.location)
    return Target(kind=item.kind, name=item.name, location=location)


def plan_delete(
    session: WorkbenchSession,
    items: list[SelectionItem],
    *,
    keep_rules: bool = False,
) -> ChangeSet:
    """Plan the reference-safe deletion of `items` as one `ChangeSet`.

    One plan covers every item. The cascade then sees the whole delete set at
    once, so a group emptied by one item and refilled by another is judged
    correctly.
    """
    graph = ReferenceGraph.build(session.working_snapshot)
    return plan_purge(
        session.working_snapshot,
        graph,
        [_target(i) for i in items],
        keep_rules=keep_rules,
    )


class DeleteScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("ctrl+y", "stage", "delete"),
        ("escape", "app.pop_screen", "cancel"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        # `check_action` refuses every hub action while a spoke is stacked, and
        # this screen has no selection-editing binding, so the captured list and
        # the live selection cannot diverge while the spoke is open. A future
        # spoke that edits the selection must re-derive here too.
        self._items = deletable_items(session)
        self._previewed = False

    def compose(self) -> ComposeResult:
        if not self._items:
            yield Static("No selected objects to delete.", id="delete-empty")
        else:
            names = ", ".join(f"{i.kind} {i.name}@{i.location}" for i in self._items)
            yield Static(f"Delete: {names}", id="delete-plan")
            yield Checkbox("keep rules left with an empty field", id="delete-keep-rules")
            yield ReviewPanel(id="review")
        yield Footer()

    def on_mount(self) -> None:
        if self._items:
            self._render_plan()

    def _keep_rules(self) -> bool:
        return self.query_one("#delete-keep-rules", Checkbox).value

    def _render_plan(self) -> None:
        panel = self.query_one("#review", ReviewPanel)
        try:
            cs = plan_delete(self.session, self._items, keep_rules=self._keep_rules())
        except PscError as exc:
            # A delete must never stage a plan the operator has not seen. Every
            # other spoke leaves a stale panel here; this one shows the failure
            # and refuses, because there is no safe reading of a blank panel.
            self._previewed = False
            panel.show(ChangeSet(title="cannot plan this delete", blockers=[str(exc)]))
            return
        self._previewed = True
        panel.show(cs)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "delete-keep-rules" and self._items:
            self._render_plan()

    def action_stage(self) -> None:
        hub = cast("WorkbenchApp", self.app)
        # Re-derive from the live selection, which can change while the spoke is
        # open (a staged plan reconciles the selection).
        items = deletable_items(self.session)
        if not items or not self._previewed:
            self.app.bell()
            return
        try:
            cs = plan_delete(self.session, items, keep_rules=self._keep_rules())
            if not can_apply(cs):
                # Stay on screen so the operator reads the blocker in the panel.
                self.app.bell()
                return
            self.session.stage(f"delete {len(items)} object(s)", cs)
        except Exception:
            self.app.bell()
            hub._refresh_selection_view()
            return
        hub._refresh_selection_view()
        self.app.pop_screen()
