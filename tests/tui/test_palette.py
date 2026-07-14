from __future__ import annotations

import pytest
from textual.app import App
from textual.command import CommandPalette, Hit
from textual.widgets import DataTable

from psc.core.source import OfflineSource
from psc.tui.app import WorkbenchApp
from psc.tui.commands import HUB_COMMANDS
from psc.tui.palette import PscCommands
from psc.tui.screens.duplicates import DuplicatesScreen
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode


def _app(workbench_xml: str) -> WorkbenchApp:
    sess = WorkbenchSession(source=OfflineSource(workbench_xml), output_mode=OutputMode.SET)
    return WorkbenchApp(sess)


def test_provider_is_registered() -> None:
    assert PscCommands in WorkbenchApp.COMMANDS


def test_system_commands_survive() -> None:
    # Theme/screenshot/quit stay reachable — they just rank below ours.
    assert App.COMMANDS <= WorkbenchApp.COMMANDS


@pytest.mark.asyncio
async def test_discover_yields_every_command_except_itself(workbench_xml: str) -> None:
    # #6: the palette must not list itself — you're already in it, and
    # picking 'Session > Commands' just dismisses the palette rather than
    # reopening it.
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        await pilot.pause()
        provider = PscCommands(app.screen)
        hits = [h async for h in provider.discover()]
        assert len(hits) == len(HUB_COMMANDS) - 1
        assert not any("Commands" in str(h.text) for h in hits)


@pytest.mark.asyncio
async def test_search_excludes_itself(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        await pilot.pause()
        provider = PscCommands(app.screen)
        hits = [h async for h in provider.search("command")]
        assert not any("Session › Commands" in str(h.text) for h in hits)  # noqa: RUF001


@pytest.mark.asyncio
async def test_discover_hits_outrank_system_commands(workbench_xml: str) -> None:
    # DiscoveryHit.score is hardcoded to 0 and the palette sorts on score alone,
    # so anything above 0 floats psc above Textual's system commands. We use
    # Hit (never DiscoveryHit) precisely to get a score at all.
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        await pilot.pause()
        provider = PscCommands(app.screen)
        hits = [h async for h in provider.discover()]
        assert all(isinstance(h, Hit) for h in hits)
        assert all(h.score > 1.0 for h in hits)


@pytest.mark.asyncio
async def test_discover_preserves_table_order(workbench_xml: str) -> None:
    # Descending scores => the palette's sort reproduces the table's category order.
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        await pilot.pause()
        provider = PscCommands(app.screen)
        scores = [h.score async for h in provider.discover()]
        assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_search_finds_a_command_by_title(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        await pilot.pause()
        provider = PscCommands(app.screen)
        hits = [h async for h in provider.search("dedup")]
        assert any("Dedup" in str(h.text) for h in hits)
        assert all(h.score > 1.0 for h in hits)


@pytest.mark.asyncio
async def test_hit_help_includes_the_hotkey(workbench_xml: str) -> None:
    # The palette used to teach you nothing about the key that runs a command
    # directly next time — the two discovery surfaces (this and the ? overlay)
    # didn't reinforce each other. help now carries the key too, driven from
    # the same table row as the title/description.
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        await pilot.pause()
        provider = PscCommands(app.screen)
        hits = [h async for h in provider.search("dedup")]
        hit = next(h for h in hits if "Analyze › Dedup" in str(h.text))  # noqa: RUF001
        assert hit.help is not None
        assert "(d)" in hit.help


@pytest.mark.asyncio
async def test_search_finds_a_command_by_description(workbench_xml: str) -> None:
    # 'survivor' appears only in dedup's description, not its title — the whole
    # reason descriptions are in the table.
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        await pilot.pause()
        provider = PscCommands(app.screen)
        hits = [h async for h in provider.search("survivor")]
        assert any("Dedup" in str(h.text) for h in hits)


@pytest.mark.asyncio
async def test_search_distinguishes_dedup_from_duplicate_scan(workbench_xml: str) -> None:
    # The original complaint: 'dedup' and 'dup scan' were indistinguishable.
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        await pilot.pause()
        provider = PscCommands(app.screen)
        hits = [h async for h in provider.search("dup")]
        texts = [str(h.text) for h in hits]
        assert any("Dedup" in t for t in texts)
        assert any("Duplicate scan" in t for t in texts)
        # Each carries its own help text, so the list explains the difference.
        helps = [h.help for h in hits if h.help]
        assert any("survivor" in h for h in helps)
        assert any("whole config" in h for h in helps)


@pytest.mark.asyncio
async def test_hits_are_labelled_with_their_category(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        await pilot.pause()
        provider = PscCommands(app.screen)
        hits = [h async for h in provider.search("dedup")]
        assert any("Analyze" in str(h.text) for h in hits)


@pytest.mark.asyncio
async def test_palette_command_runs_the_hub_action(workbench_xml: str) -> None:
    # Callback-level check that a Hit's command actually opens the spoke.
    # NOT end-to-end: no palette is open, so check_action sees stack == 1 for
    # a trivial reason (there's nothing on top of the hub to begin with) —
    # see test_ctrl_p_command_opens_the_spoke_end_to_end below for the real
    # thing, which drives the actual CommandPalette widget and is what
    # exercises the dismiss-before-invoke ordering the design relies on.
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        await pilot.pause()
        provider = PscCommands(app.screen)
        hits = [h async for h in provider.discover()]
        hit = next(h for h in hits if "Duplicate scan" in str(h.text))
        # In the real palette this runs via `app.call_later(hit.command)`, which
        # awaits it through the message pump (textual/message_pump.py `invoke`).
        # `run_action` is itself a coroutine function, so mirror that here.
        await hit.command()
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, DuplicatesScreen)


@pytest.mark.asyncio
async def test_ctrl_p_command_opens_the_spoke_end_to_end(workbench_xml: str) -> None:
    # The real path: open the actual CommandPalette from the hub, type to
    # filter it, pick a hit, and confirm the spoke actually opens. This is
    # what test_palette_command_runs_the_hub_action above does NOT cover —
    # that test builds PscCommands by hand and calls hit.command() with no
    # palette open, so check_action trivially sees a bare hub. The design
    # depends on CommandPalette._select_or_command calling self.dismiss()
    # *before* self.app.call_later(...) (textual/command.py) — if that
    # ordering ever regressed, the palette screen would still be on the stack
    # when the action's check_action ran, and the spoke-stacking guard would
    # silently swallow the action instead of opening it. Only a pilot that
    # drives the real widget can see that.
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()  # off the search Input
        await pilot.press("ctrl+p")
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)
        for key in "duplicate":
            await pilot.press(key)
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, DuplicatesScreen)


@pytest.mark.asyncio
async def test_ctrl_p_opens_the_palette_from_the_hub(workbench_xml: str) -> None:
    # #1, half (a): ctrl+p must still work from a bare hub — it's only spokes
    # it should be gated against.
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()  # off the search Input
        await pilot.press("ctrl+p")
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)


@pytest.mark.asyncio
async def test_ctrl_p_is_inert_over_an_open_spoke(workbench_xml: str) -> None:
    # #1, half (b): the palette used to be hub_only=False, so ctrl+p opened it
    # over an open spoke (e.g. DuplicatesScreen) and offered commands whose
    # target action check_action would refuse — picking one silently
    # dismissed the palette and did nothing, with no error and no feedback.
    # Gating command_palette like every other hub action fixes this: ctrl+p
    # now does nothing at all while a spoke is open, same as 'd' or 'c'.
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()  # off the search Input
        await pilot.press("D")
        await pilot.pause()
        assert isinstance(app.screen, DuplicatesScreen)
        await pilot.press("ctrl+p")
        await pilot.pause()
        assert [type(s).__name__ for s in app.screen_stack] == ["Screen", "DuplicatesScreen"]
