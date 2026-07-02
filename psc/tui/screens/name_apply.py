"""Name-apply spoke: bulk reference-aware rename-to-scheme (a MUTATION).

Parity for the CLI `name apply --all` (issue #15): every non-compliant object is
renamed to its scheme name in ONE ChangeSet, with references repointed. A plan
that would collide or shadow carries blockers and is hard-gated — never staged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer

from psc.core.changeset import ChangeSet
from psc.core.naming import NamingScheme, plan_apply_scheme
from psc.core.refs import ReferenceGraph
from psc.tui.session import WorkbenchSession
from psc.tui.widgets.review import ReviewPanel, can_apply

if TYPE_CHECKING:
    from psc.tui.app import WorkbenchApp

_LABEL = "apply naming scheme to all non-compliant objects"


def plan_scheme(session: WorkbenchSession) -> ChangeSet:
    """Bulk rename-to-scheme plan for every non-compliant object (default scheme)."""
    snap = session.working_snapshot
    graph = ReferenceGraph.build(snap)
    return plan_apply_scheme(snap, graph, NamingScheme())


class NameApplyScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("ctrl+y", "stage", "stage renames"),
        ("escape", "app.pop_screen", "cancel"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        yield ReviewPanel(id="review")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#review", ReviewPanel).show(plan_scheme(self.session))

    def action_stage(self) -> None:
        # Re-plan against the CURRENT snapshot; a blocked/engine-erroring plan
        # bells and never stages (safety hard gate), never crashes the app.
        try:
            cs = plan_scheme(self.session)
            if not can_apply(cs):
                self.query_one("#review", ReviewPanel).show(cs)  # surface blockers
                self.app.bell()
                return
            self.session.stage(_LABEL, cs)
        except Exception:
            self.app.bell()
            return
        cast("WorkbenchApp", self.app)._refresh_selection_view()
        self.app.pop_screen()
