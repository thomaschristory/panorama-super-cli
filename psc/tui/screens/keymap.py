"""The `?` overlay — every key binding, grouped, on demand.

The Footer only advertises three keys; this is where the other twenty live. It
reads the same `psc.tui.commands` table the bindings are generated from, so it
can never drift out of sync with what the keys actually do.
"""

from __future__ import annotations

from typing import ClassVar

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from psc.tui.commands import by_category

# The BINDINGS below are the only way out of this modal; say so on the card
# itself, since the Footer's `? keys` entry is disabled while it's open (the
# spoke-stacking guard in check_action) and has nothing to point at.
DISMISS_HINT = "esc / ? / q — close"


def _render() -> Table:
    """The cheatsheet body: 'Category' header rows, then key/title/description rows.

    A flat `\\n`-joined string left to a Static's soft-wrap put a long
    description's continuation line back at column 0, under the key column —
    it read as broken text rather than a cheatsheet. A Table keeps the wrap
    inside the description column, with continuation lines hanging under it.
    """
    table = Table(
        box=None,
        show_header=False,
        pad_edge=True,
        padding=(0, 1),
        caption=DISMISS_HINT,
        caption_style="dim",
        caption_justify="left",
    )
    table.add_column("key", style="b", no_wrap=True, width=10)
    table.add_column("title", no_wrap=True)
    table.add_column("description")  # the only column allowed to wrap

    first = True
    for category, cmds in by_category():
        if not cmds:
            continue
        if not first:
            table.add_row()
        first = False
        table.add_row(Text(category, style="b #fa582d"))
        for cmd in cmds:
            description = Text("— ")
            description.append(cmd.description, style="dim")
            table.add_row(cmd.key, cmd.title, description)
    return table


class KeymapScreen(ModalScreen[None]):
    """A centred card over the dimmed hub listing every binding."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("escape", "close", "close"),
        ("question_mark", "close", "close"),
        ("q", "close", "close"),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="keymap-card"), VerticalScroll():
            yield Static(_render(), id="keymap-body")

    def action_close(self) -> None:
        self.dismiss(None)
