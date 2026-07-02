from __future__ import annotations

import pytest
from textual.widgets import DataTable, Input, Select

from psc.core.source import OfflineSource
from psc.tui.app import WorkbenchApp
from psc.tui.screens.audit import AuditScreen
from psc.tui.screens.move import MoveScreen
from psc.tui.screens.rename import RenameScreen
from psc.tui.screens.rule import RuleScreen
from psc.tui.screens.usage import UsageScreen
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode


def _app(workbench_xml: str) -> WorkbenchApp:
    sess = WorkbenchSession(source=OfflineSource(workbench_xml), output_mode=OutputMode.SET)
    return WorkbenchApp(sess)


@pytest.mark.asyncio
async def test_search_populates_results(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "srv"
        await pilot.press("enter")
        await pilot.pause()
        table = app.query_one("#results", DataTable)
        assert table.row_count == 2


@pytest.mark.asyncio
async def test_results_show_value_column(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        # web-srv-01 (10.0.5.10/32) and web-srv-02 (10.0.5.10/32) share a prefix;
        # db-gw (10.0.9.1/32) does not. Searching "srv" returns the two web rows,
        # each of which must carry its ip-netmask value so they're distinguishable.
        app.query_one("#search", Input).value = "web"
        await pilot.press("enter")
        await pilot.pause()
        table = app.query_one("#results", DataTable)
        headers = [str(c.label) for c in table.columns.values()]
        assert "value" in headers
        value_key = next(k for k, c in table.columns.items() if str(c.label) == "value")
        values = {str(table.get_cell(rk, value_key)) for rk in table.rows}
        assert values == {"10.0.5.10/32"}


@pytest.mark.asyncio
async def test_space_toggles_selection(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "db-gw"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")
        await pilot.pause()
        assert [i.name for i in app.session.selection] == ["db-gw"]


@pytest.mark.asyncio
async def test_space_twice_deselects(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "db-gw"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        assert app.session.selection == []


@pytest.mark.asyncio
async def test_dedup_spoke_stages_merge_and_reconciles(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "10.0.5.10"
        await pilot.press("enter")
        await pilot.pause()
        results = app.query_one("#results", DataTable)
        results.focus()
        await pilot.press("space")  # row 0
        results.move_cursor(row=1)
        await pilot.press("space")  # row 1
        await pilot.pause()
        assert len(app.session.selection) == 2
        await pilot.press("d")  # open dedup screen
        await pilot.pause()
        await pilot.press("ctrl+y")  # stage the proposed merge
        await pilot.pause()
        assert len(app.session.staging) == 1
        assert len(app.session.selection) == 1  # reconciled: merged-away dupe gone


@pytest.mark.asyncio
async def test_apply_batch_offline_writes_file(workbench_xml: str, tmp_path) -> None:
    sess = WorkbenchSession(
        source=OfflineSource(workbench_xml), output_mode=OutputMode.OFFLINE_APPLY
    )
    dest = tmp_path / "candidate.xml"
    sess.apply_out_path = str(dest)  # the hub reads this for offline apply
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "10.0.5.10"
        await pilot.press("enter")
        await pilot.pause()
        results = app.query_one("#results", DataTable)
        results.focus()
        await pilot.press("space")
        results.move_cursor(row=1)
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("ctrl+y")
        await pilot.pause()
        await pilot.press("ctrl+a")  # apply batch
        await pilot.pause()
    assert dest.exists()
    assert "web-srv-02" not in dest.read_text()


@pytest.mark.asyncio
async def test_usage_spoke_opens_from_hub(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "web-srv-01"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("u")
        await pilot.pause()
        assert isinstance(app.screen, UsageScreen)
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_audit_spoke_opens_from_hub(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "web-srv-01"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, AuditScreen)
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_decommission_spoke_stages_from_hub(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "db-gw"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert len(app.session.staging) == 1
        assert app.session.selection == []


@pytest.mark.asyncio
async def test_rename_spoke_opens_from_hub(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "db-gw"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        assert isinstance(app.screen, RenameScreen)


@pytest.mark.asyncio
async def test_move_and_rule_bindings_open(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "db-gw"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        assert isinstance(app.screen, MoveScreen)
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, RuleScreen)


@pytest.mark.asyncio
async def test_rename_spoke_stages_and_reconciles(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "db-gw"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        app.screen.query_one("#rename-input", Input).value = "db-gateway"
        await pilot.press("enter")
        await pilot.pause()
        assert len(app.session.staging) == 1
        # db-gw renamed -> its old identity drops out of the selection
        assert app.session.selection == []


@pytest.mark.asyncio
async def test_rename_picks_chosen_entry_not_first(workbench_xml: str) -> None:
    # Two objects selected: the rename spoke must rename the one the user PICKS
    # in the target dropdown, not silently the first (#89).
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "web-srv"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")  # select web-srv-01 (row 0)
        await pilot.press("down")
        await pilot.press("space")  # select web-srv-02 (row 1)
        await pilot.pause()
        assert len(app.session.selection) == 2
        await pilot.press("r")
        await pilot.pause()
        app.screen.query_one("#rename-target", Select).value = 1  # choose the 2nd
        app.screen.query_one("#rename-input", Input).value = "web-server-02"
        await pilot.press("enter")
        await pilot.pause()
        assert len(app.session.staging) == 1
        assert "web-srv-02" in app.session.staging[0].label
        # the un-renamed first object is still selected
        assert any(i.name == "web-srv-01" for i in app.session.selection)


@pytest.mark.asyncio
async def test_move_spoke_stages_and_reconciles(workbench_xml_dg: str) -> None:
    sess = WorkbenchSession(source=OfflineSource(workbench_xml_dg), output_mode=OutputMode.SET)
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "dg-only"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert len(app.session.staging) == 1
        # dg-only moved dg1 -> shared; the dg1 identity drops out of the selection
        assert app.session.selection == []


@pytest.mark.asyncio
async def test_move_dest_select_defaults_shared_and_lists_dgs(workbench_xml_two_dg: str) -> None:
    sess = WorkbenchSession(source=OfflineSource(workbench_xml_two_dg), output_mode=OutputMode.SET)
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "dg-only"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        assert isinstance(app.screen, MoveScreen)
        select = app.screen.query_one("#move-dest", Select)
        assert select.value == "shared"
        options = [value for _label, value in select._options]  # (prompt, value) pairs
        assert options == ["shared", "dg1", "dg2"]


@pytest.mark.asyncio
async def test_move_to_chosen_dg_stages_that_destination(workbench_xml_two_dg: str) -> None:
    # A DG that IS an ancestor of the source is a valid promote target. With only
    # sibling DGs here, shared is the sole valid non-source dest; choosing dg2
    # (non-ancestor) must bell and stage nothing. Verify the dest threads through:
    # picking shared explicitly stages a move to shared.
    sess = WorkbenchSession(source=OfflineSource(workbench_xml_two_dg), output_mode=OutputMode.SET)
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "dg-only"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        # Set a non-ancestor dest programmatically: the move is blocked -> bell,
        # nothing staged, still on the move screen.
        app.screen.query_one("#move-dest", Select).value = "dg2"
        await pilot.pause()
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert isinstance(app.screen, MoveScreen)
        assert app.session.staging == []
        # Now pick shared and confirm: the dest threads through and stages.
        app.screen.query_one("#move-dest", Select).value = "shared"
        await pilot.pause()
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert len(app.session.staging) == 1
        assert app.session.staging[0].label == "move dg-only -> shared"
