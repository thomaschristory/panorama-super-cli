"""The ctrl+p command palette source.

Textual's default palette just dumps the binding labels, which is why `dedup` and
`dup scan` used to be indistinguishable. This yields the real titles, categories
and descriptions from `psc.tui.commands`.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from textual.command import Hit, Hits, Provider

from psc.tui.commands import HUB_COMMANDS, Command

# The palette sorts hits by `Hit.score` alone, and Textual's system-command
# provider yields DiscoveryHit — whose score is hardcoded to 0. The fuzzy matcher
# returns 0..1. So scoring psc hits above 1.0 is what keeps them above the system
# commands in both the empty-query list and the search results. This is the only
# lever available: providers are queried concurrently, so ties break arbitrarily.
_PSC_FLOOR = 1.0


class PscCommands(Provider):
    """Every hub command, searchable by title, category or description."""

    def _runner(self, command: Command) -> Callable[[], object]:
        # run_action goes through check_action, so a command is inert over a
        # spoke — but the palette dismisses itself before invoking this, so from
        # the hub it always runs.
        return partial(self.app.run_action, command.action)

    def _label(self, command: Command) -> str:
        return f"{command.category} › {command.title}"  # noqa: RUF001

    async def discover(self) -> Hits:
        """The empty-query list: the whole table, in table order."""
        # Exclude the palette's own row — you're already in it, and picking it
        # would just dismiss the palette instead of reopening it.
        rows = [c for c in HUB_COMMANDS if c.action != "command_palette"]
        total = len(rows)
        for i, command in enumerate(rows):
            # Descending within (1, 2] so the sort reproduces the table's order.
            yield Hit(
                _PSC_FLOOR + (total - i) / total,
                self._label(command),
                self._runner(command),
                help=command.description,
            )

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for command in HUB_COMMANDS:
            if command.action == "command_palette":
                continue
            label = self._label(command)
            # Match the description too, so 'survivor' finds dedup even though the
            # word appears nowhere in its title.
            score = max(matcher.match(label), matcher.match(command.description))
            if score > 0:
                yield Hit(
                    _PSC_FLOOR + score,
                    matcher.highlight(label),
                    self._runner(command),
                    text=label,
                    help=command.description,
                )
