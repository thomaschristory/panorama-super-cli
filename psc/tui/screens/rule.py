"""Rule spoke: add selected objects as members of an existing rule field."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Input, Static

from psc.core.changeset import ChangeSet
from psc.core.models import Rulebase
from psc.core.rule_edit import plan_rule_member_edit
from psc.output.errors import PscError
from psc.tui.session import WorkbenchSession
from psc.tui.widgets.review import can_apply

if TYPE_CHECKING:
    from psc.tui.app import WorkbenchApp

_DEFAULT_FIELD = "source"


def plan_rule_add_member(
    session: WorkbenchSession,
    rule_name: str,
    rulebase: Rulebase,
    field: str,
    member_name: str,
) -> ChangeSet:
    """Plan adding `member_name` to `rule_name`'s `field` (idempotent)."""
    return plan_rule_member_edit(
        session.working_snapshot,
        rule_name,
        None,
        rulebase,
        field,
        add=member_name,
        remove=None,
    )


class RuleScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("escape", "app.pop_screen", "cancel"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        self._members = [i.name for i in session.selection]

    def compose(self) -> ComposeResult:
        if not self._members:
            yield Static("Select objects to add to a rule first.", id="rule-empty")
        else:
            names = ", ".join(self._members)
            yield Static(f"Add [{names}] to a pre-rulebase security rule's field.")
            yield Input(placeholder="rule name", id="rule-name")
            yield Input(
                value=_DEFAULT_FIELD,
                placeholder="field (source/destination/service)",
                id="rule-field",
            )
            yield Static("Fill both, then press Enter on the field box to stage.", id="rule-hint")
        yield Footer()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "rule-field" or not self._members:
            return
        rule_name = self.query_one("#rule-name", Input).value.strip()
        field = event.value.strip() or _DEFAULT_FIELD
        if not rule_name:
            self.app.bell()
            return
        try:
            for member in list(self._members):
                cs = plan_rule_add_member(self.session, rule_name, Rulebase.PRE, field, member)
                if not can_apply(cs):
                    self.app.bell()
                    break
                if not cs.is_empty:
                    self.session.stage(f"add {member} to {rule_name}.{field}", cs)
        except PscError:
            # Unknown rule / invalid field: signal and stay on the screen.
            self.app.bell()
            return
        self.app.pop_screen()
        cast("WorkbenchApp", self.app)._refresh_selection_view()
