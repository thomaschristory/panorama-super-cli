"""Staged-changes spoke: inspect the staged changelist + drop a single change.

The hub only shows a `staged (N)` counter and apply is all-or-nothing. This
screen lists every staged change with its plan summary, lets the operator open
one to see its full set-script, and drop a single change without discarding the
whole batch (`session.drop_staged`). Read-only apart from that drop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.content import Content
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from psc.core.setcmd import render_changeset
from psc.output.errors import PscError
from psc.tui.session import WorkbenchSession

if TYPE_CHECKING:
    from psc.tui.app import WorkbenchApp


def staged_detail(session: WorkbenchSession, index: int) -> str:
    """Full detail for one staged change: its label/title + rendered set-script."""
    if not 0 <= index < len(session.staging):
        return ""
    staged = session.staging[index]
    cs = staged.changeset
    lines = [f"{staged.label}  [{cs.title}]", ""]
    lines.extend(render_changeset(cs))
    return "\n".join(lines)


class StagedScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("d", "drop", "drop change"),
        ("ctrl+a", "apply", "apply"),
        ("escape", "app.pop_screen", "back"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        table: DataTable[str] = DataTable(id="staged-table")
        yield table
        yield Static("", id="staged-detail")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#staged-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("#", "label", "summary")
        self._refresh()

    def _refresh(self) -> None:
        """Rebuild the table + detail from the current staging list."""
        table = self.query_one("#staged-table", DataTable)
        table.clear()
        for i, staged in enumerate(self.session.staging):
            summary = "; ".join(staged.changeset.summaries()) or staged.changeset.title
            table.add_row(str(i), staged.label, summary)
        self._show_detail()

    def _show_detail(self) -> None:
        detail = self.query_one("#staged-detail", Static)
        if not self.session.staging:
            detail.update("No staged changes.")
            return
        table = self.query_one("#staged-table", DataTable)
        row = table.cursor_row if table.cursor_row is not None else 0
        # Render as plain Content: the set-script text contains `[ ... ]` member
        # lists that Textual's markup engine would otherwise eat (#129).
        detail.update(Content(staged_detail(self.session, row)))

    def on_data_table_row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        # Inspect: the detail panel tracks the highlighted change's full set-script.
        self._show_detail()

    def on_screen_resume(self) -> None:
        # Returning from the apply screen: a committing apply may have cleared or
        # changed the batch, so rebuild the list to reflect the current staging.
        self._refresh()

    def action_apply(self) -> None:
        # Apply is reachable ONLY from here (#127): the batch must be reviewed on
        # the staged changelist before it can be emitted. The ApplyScreen owns the
        # format/destination choice and the actual apply_batch call.
        from psc.tui.screens.apply import ApplyScreen  # noqa: PLC0415 — avoid import cycle

        self.app.push_screen(ApplyScreen(self.session))

    def action_drop(self) -> None:
        table = self.query_one("#staged-table", DataTable)
        if not self.session.staging:
            self.app.bell()
            return
        try:
            self.session.drop_staged(table.cursor_row)
        except PscError:
            # A dropped change was a dependency of a later one; the batch is kept
            # intact. Signal and leave the screen unchanged.
            self.app.bell()
            return
        self._refresh()
        # Keep the hub's `staged (N)` strip in sync with the drop.
        cast("WorkbenchApp", self.app)._refresh_selection_view()
