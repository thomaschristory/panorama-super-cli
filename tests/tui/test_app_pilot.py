from __future__ import annotations

import pytest
from textual.widgets import Checkbox, DataTable, Input, Select, Static

from psc.core.changeset import ObjectKind
from psc.core.models import AddressType
from psc.core.source import OfflineSource
from psc.tui.app import WorkbenchApp
from psc.tui.screens.audit import AuditScreen
from psc.tui.screens.create import SERVICE_PROTOCOLS, CreateScreen
from psc.tui.screens.dangling import DanglingScreen
from psc.tui.screens.dedup import DedupScreen
from psc.tui.screens.lint import LintScreen
from psc.tui.screens.move import MoveScreen
from psc.tui.screens.name_apply import NameApplyScreen
from psc.tui.screens.rename import RenameScreen
from psc.tui.screens.rule import RuleScreen
from psc.tui.screens.staged import StagedScreen
from psc.tui.screens.unused import UnusedScreen
from psc.tui.screens.usage import UsageScreen
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode
from psc.tui.widgets.review import ReviewPanel


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
async def test_dedup_spoke_collapses_three_toward_chosen_survivor(
    workbench_xml_triple: str,
) -> None:
    # Select three duplicate addresses, open dedup, pick a survivor in the Select,
    # confirm -> ONE staged change that keeps the survivor and removes the others.
    sess = WorkbenchSession(source=OfflineSource(workbench_xml_triple), output_mode=OutputMode.SET)
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "10.0.5.10"
        await pilot.press("enter")
        await pilot.pause()
        results = app.query_one("#results", DataTable)
        results.focus()
        await pilot.press("space")  # row 0
        results.move_cursor(row=1)
        await pilot.press("space")  # row 1
        results.move_cursor(row=2)
        await pilot.press("space")  # row 2
        await pilot.pause()
        assert len(app.session.selection) == 3
        await pilot.press("d")  # open dedup screen
        await pilot.pause()
        # Pick web-srv-02 as the survivor via the keep Select.
        keep_select = app.screen.query_one("#dedup-keep", Select)
        idx = next(i for i, (label, _v) in enumerate(keep_select._options) if "web-srv-02" in label)
        keep_select.value = keep_select._options[idx][1]
        await pilot.pause()
        await pilot.press("ctrl+y")  # stage the collapse
        await pilot.pause()
        assert len(app.session.staging) == 1
        names = {a.name for a in app.session.working_snapshot.addresses}
        assert "web-srv-02" in names  # survivor remains
        assert "web-srv-01" not in names and "web-srv-03" not in names  # others gone


@pytest.mark.asyncio
async def test_dedup_cascade_checkbox_absent_for_address_bucket(workbench_xml: str) -> None:
    # An address bucket has no in-place dependency to cascade (`compose` only
    # yields the checkbox for ObjectKind.ADDRESS_GROUP) — confirm it stays off.
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
        await pilot.press("d")  # open dedup screen
        await pilot.pause()
        assert isinstance(app.screen, DedupScreen)
        assert list(app.screen.query("#dedup-cascade")) == []


@pytest.mark.asyncio
async def test_dedup_cascade_checkbox_toggles_group_bucket_plan(
    session_with_dup_groups: WorkbenchSession,
) -> None:
    # Two sibling device-groups' 'web' address-groups (each over its own local
    # 'h-web1') form an address-group bucket: the cascade checkbox must appear,
    # and ticking it must pull the leaf address into the review panel's plan.
    app = WorkbenchApp(session_with_dup_groups)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()  # focus off the search Input
        await pilot.press("d")  # open dedup screen
        await pilot.pause()
        assert isinstance(app.screen, DedupScreen)
        checkbox = app.screen.query_one("#dedup-cascade", Checkbox)
        assert checkbox.value is False

        dest = app.screen.query_one("#dedup-dest", Select)
        dest.value = "shared"
        await pilot.pause()

        review = app.screen.query_one("#review", ReviewPanel)
        before_cs = review._cs
        assert before_cs.is_blocked  # h-web1 isn't visible at shared without cascade
        assert not any(u.kind is ObjectKind.ADDRESS for u in before_cs.upserts)

        checkbox.value = True
        await pilot.pause()

        after_cs = review._cs
        assert not after_cs.is_blocked
        # The leaf address came up too, alongside the promoted group.
        assert any(u.kind is ObjectKind.ADDRESS for u in after_cs.upserts)
        assert any(u.kind is ObjectKind.ADDRESS_GROUP for u in after_cs.upserts)


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
        results.focus()
        await pilot.press("s")  # open the staged changelist (#127)
        await pilot.pause()
        await pilot.press("ctrl+a")  # open the apply picker (default = offline-full)
        await pilot.pause()
        await pilot.press("ctrl+a")  # confirm/apply (dest does not exist yet)
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
async def test_unused_spoke_opens_and_lists(workbench_xml_refs: str) -> None:
    app = _app(workbench_xml_refs)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()  # focus off the search Input
        await pilot.press("i")  # open the unused spoke
        await pilot.pause()
        assert isinstance(app.screen, UnusedScreen)
        table = app.screen.query_one("#unused-table", DataTable)
        assert table.row_count >= 1  # db-gw / net-10-0-5 are unused
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_dangling_spoke_opens_and_lists(workbench_xml_dangling: str) -> None:
    app = _app(workbench_xml_dangling)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("g")  # open the dangling spoke
        await pilot.pause()
        assert isinstance(app.screen, DanglingScreen)
        table = app.screen.query_one("#dangling-table", DataTable)
        assert table.row_count == 1  # web-pool -> ghost-host
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_lint_spoke_opens_and_lists(workbench_xml_refs: str) -> None:
    app = _app(workbench_xml_refs)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("l")  # open the name-lint spoke
        await pilot.pause()
        assert isinstance(app.screen, LintScreen)
        table = app.screen.query_one("#lint-table", DataTable)
        assert table.row_count >= 1  # db-gw drifts from H-10.0.9.1
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_name_apply_spoke_stages_scheme(workbench_xml_refs: str) -> None:
    app = _app(workbench_xml_refs)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("n")  # open the name-apply spoke
        await pilot.pause()
        assert isinstance(app.screen, NameApplyScreen)
        await pilot.press("ctrl+y")  # stage the bulk rename-to-scheme
        await pilot.pause()
        assert len(app.session.staging) == 1
        new_names = {a.name for a in app.session.working_snapshot.addresses}
        assert "H-10.0.9.1" in new_names  # db-gw renamed to its scheme name


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
async def test_delete_removes_focused_item_from_selection_panel(workbench_xml: str) -> None:
    # Focus the selection panel and press delete to drop just that one item (#91),
    # without wiping the whole selection.
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "web-srv"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")
        await pilot.press("down")
        await pilot.press("space")
        await pilot.pause()
        assert len(app.session.selection) == 2
        # Focus the selection panel and delete the first (cursor) row.
        app.query_one("#selection", DataTable).focus()
        await pilot.press("delete")
        await pilot.pause()
        assert len(app.session.selection) == 1


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


@pytest.mark.asyncio
async def test_create_spoke_stages_new_address(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()  # move focus off the search Input
        await pilot.press("c")  # open the create spoke
        await pilot.pause()
        assert isinstance(app.screen, CreateScreen)
        app.screen.query_one("#create-kind", Select).value = "address"
        app.screen.query_one("#create-name", Input).value = "new-host"
        app.screen.query_one("#create-type", Select).value = "ip-netmask"
        app.screen.query_one("#create-value", Input).value = "10.9.9.9/32"
        await pilot.pause()
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert len(app.session.staging) == 1
        # Staging compounds onto working_xml: the new object is now in the snapshot.
        names = {a.name for a in app.session.working_snapshot.addresses}
        assert "new-host" in names


@pytest.mark.asyncio
async def test_create_type_and_protocol_are_dropdowns(workbench_xml: str) -> None:
    # Predefined value sets are dropdowns (not free-text): you can't type an
    # invalid address type or service protocol.
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("c")
        await pilot.pause()
        type_sel = app.screen.query_one("#create-type", Select)
        proto_sel = app.screen.query_one("#create-protocol", Select)
        color_sel = app.screen.query_one("#create-color", Select)
        assert [v for _, v in type_sel._options if v is not Select.BLANK] == [
            t.value for t in AddressType
        ]
        assert [v for _, v in proto_sel._options if v is not Select.BLANK] == list(
            SERVICE_PROTOCOLS
        )
        # Optional color can be left blank; type/protocol default to a real value.
        assert color_sel.is_blank()
        assert type_sel.value == "ip-netmask"
        assert proto_sel.value == "tcp"


@pytest.mark.asyncio
async def test_create_service_via_protocol_dropdown(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("c")
        await pilot.pause()
        app.screen.query_one("#create-kind", Select).value = "service"
        app.screen.query_one("#create-name", Input).value = "svc-https"
        app.screen.query_one("#create-protocol", Select).value = "udp"
        app.screen.query_one("#create-dest-port", Input).value = "443"
        await pilot.pause()
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert len(app.session.staging) == 1
        svc = next(s for s in app.session.working_snapshot.services if s.name == "svc-https")
        assert svc.protocol == "udp"
        assert svc.destination_port == "443"


@pytest.mark.asyncio
async def test_create_menu_is_dynamic_per_kind(workbench_xml: str) -> None:
    # The form shows only the fields the selected kind uses, and updates live
    # when the kind changes.
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("c")
        await pilot.pause()

        def shown(key: str) -> bool:
            return bool(app.screen.query_one(f"#create-{key}").display)

        # address (default): type/value shown; members/color/protocol hidden.
        assert shown("type") and shown("value")
        assert not shown("members") and not shown("color") and not shown("protocol")
        # switch to tag: color/comments shown; type/value hidden.
        app.screen.query_one("#create-kind", Select).value = "tag"
        await pilot.pause()
        assert shown("color") and shown("comments")
        assert not shown("type") and not shown("value")
        # switch to service: protocol/ports shown; members hidden.
        app.screen.query_one("#create-kind", Select).value = "service"
        await pilot.pause()
        assert shown("protocol") and shown("dest-port") and shown("source-port")
        assert not shown("members")
        # switch to address-group: members/filter shown; type hidden.
        app.screen.query_one("#create-kind", Select).value = "address-group"
        await pilot.pause()
        assert shown("members") and shown("filter")
        assert not shown("type")


@pytest.mark.asyncio
async def test_create_tag_color_dropdown_and_blank(workbench_xml: str) -> None:
    # A picked color flows through; an untouched (blank) color stays unset — the
    # is_blank() path, not a stringified sentinel.
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("c")
        await pilot.pause()
        app.screen.query_one("#create-kind", Select).value = "tag"
        app.screen.query_one("#create-name", Input).value = "t-red"
        app.screen.query_one("#create-color", Select).value = "color5"
        await pilot.pause()
        await pilot.press("ctrl+y")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("c")
        await pilot.pause()
        app.screen.query_one("#create-kind", Select).value = "tag"
        app.screen.query_one("#create-name", Input).value = "t-plain"
        await pilot.pause()
        await pilot.press("ctrl+y")
        await pilot.pause()
    tags = {t.name: t for t in app.session.working_snapshot.tags}
    assert tags["t-red"].color == "color5"
    assert tags["t-plain"].color is None


@pytest.mark.asyncio
async def test_create_spoke_blocked_does_not_stage(workbench_xml: str) -> None:
    # Creating an address whose name collides with an existing service-group... use
    # an address-group over an existing address name for the cross-kind clash.
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()  # move focus off the search Input
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, CreateScreen)
        app.screen.query_one("#create-kind", Select).value = "address-group"
        # "web-srv-01" already exists as an address -> cross-kind collision blocker.
        app.screen.query_one("#create-name", Input).value = "web-srv-01"
        app.screen.query_one("#create-members", Input).value = "db-gw"
        await pilot.pause()
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert app.session.staging == []
        assert isinstance(app.screen, CreateScreen)  # stays open after the bell


@pytest.mark.asyncio
async def test_staged_screen_lists_and_drops_a_change(workbench_xml: str) -> None:
    # Stage a rename, open the staged-changes screen, confirm the change is listed
    # with its label, drop it, and check the batch shrank + the hub strip updated.
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

        await pilot.press("s")  # open the staged-changes screen
        await pilot.pause()
        assert isinstance(app.screen, StagedScreen)
        table = app.screen.query_one("#staged-table", DataTable)
        assert table.row_count == 1
        label = app.session.staging[0].label
        cells = {str(table.get_cell(rk, ck)) for rk in table.rows for ck in table.columns}
        assert any(label in c for c in cells)

        await pilot.press("d")  # drop the focused staged change
        await pilot.pause()
        assert app.session.staging == []
        assert isinstance(app.screen, StagedScreen)  # stays open (now empty)
        await pilot.press("escape")
        await pilot.pause()
        # Hub staging strip reflects the drop.
        strip = app.query_one("#staging", Static)
        assert "staged (0)" in str(strip.render())
