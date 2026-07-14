"""The `?` overlay — every key binding, grouped, on demand.

The Footer only advertises three keys; this is where the other twenty live. It
reads the same `psc.tui.commands` table the bindings are generated from, so it
can never drift out of sync with what the keys actually do.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from psc.tui.commands import by_category


def _render() -> str:
    """The cheatsheet body: 'Category' headers, then '  key  Title — description'."""
    lines: list[str] = []
    for category, cmds in by_category():
        if not cmds:
            continue
        if lines:
            lines.append("")
        lines.append(f"[b #fa582d]{category}[/]")
        for cmd in cmds:
            # Markup-escape nothing here: keys/titles/descriptions are ours, not
            # user data. (Contrast the group screens, which show config names.)
            lines.append(f"  [b]{cmd.key:<10}[/] {cmd.title} — [dim]{cmd.description}[/]")
    return "\n".join(lines)


class KeymapScreen(ModalScreen[None]):
    """A centred card over the dimmed hub listing every binding."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("escape", "close", "close"),
        ("question_mark", "close", "close"),
        ("q", "close", "close"),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="keymap-card"), VerticalScroll():
            yield Static(_render(), id="keymap-body", markup=True)

    def action_close(self) -> None:
        self.dismiss(None)
