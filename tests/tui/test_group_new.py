"""The `N` spoke: build a group out of the find session's selection (#146)."""

from __future__ import annotations

import pytest
from textual.widgets import DataTable, Input, Select, Static

from psc.core.source import OfflineSource
from psc.tui.app import WorkbenchApp
from psc.tui.screens.create import CreateScreen
from psc.tui.screens.group_new import NewGroupScreen, plan_new_group
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode, SelectionItem


def _session(path: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(path), output_mode=OutputMode.SET)


def _plain(widget: Static) -> str:
    """The text a Static actually shows (markup stripped)."""
    rendered = widget.render()
    return rendered.plain if hasattr(rendered, "plain") else str(rendered)


def _focus_hub(app: WorkbenchApp) -> None:
    """Move focus off the search Input so a hub key is a binding, not a keystroke."""
    app.query_one("#results", DataTable).focus()


# --- plan_new_group (no app needed) ----------------------------------------


def test_plan_new_group_from_addresses(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    sess.add(SelectionItem(kind="address", name="web-srv-01", location="shared"))
    sess.add(SelectionItem(kind="address", name="web-srv-02", location="shared"))
    cs = plan_new_group(sess, "web-pool", "shared")
    assert not cs.is_blocked, cs.blockers
    assert cs.upserts[0].kind.value == "address-group"
    assert cs.upserts[0].members == ["web-srv-01", "web-srv-02"]


def test_plan_new_group_from_services_derives_service_group(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    sess.add(SelectionItem(kind="service", name="tcp-8443", location="shared"))
    cs = plan_new_group(sess, "web-ports", "shared")
    assert cs.upserts[0].kind.value == "service-group"


def test_plan_new_group_skips_tags(workbench_xml: str) -> None:
    # A tag can't be a group member; it is dropped from the selection rather than
    # exploding the plan (the screen reports the count it left behind).
    sess = _session(workbench_xml)
    sess.add(SelectionItem(kind="address", name="db-gw", location="shared"))
    sess.add(SelectionItem(kind="tag", name="prod", location="shared"))
    cs = plan_new_group(sess, "db-pool", "shared")
    assert cs.upserts[0].members == ["db-gw"]


# --- the spoke -------------------------------------------------------------


async def _select(pilot, app: WorkbenchApp, query: str, count: int = 1) -> None:
    """Search `query` and toggle the first `count` result rows into the selection."""
    app.query_one("#search", Input).value = query
    await pilot.press("enter")
    await pilot.pause()
    app.query_one("#results", DataTable).focus()
    for _ in range(count):
        await pilot.press("space")
        await pilot.press("down")
        await pilot.pause()


@pytest.mark.asyncio
async def test_n_creates_a_group_from_the_selection(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        await _select(pilot, app, "web", count=2)
        assert len(sess.selection) == 2
        await pilot.press("N")
        await pilot.pause()
        assert isinstance(app.screen, NewGroupScreen)
        app.screen.query_one("#group-new-name", Input).value = "web-pool"
        await pilot.press("ctrl+y")
        await pilot.pause()

        grp = next(g for g in sess.working_snapshot.address_groups if g.name == "web-pool")
        assert grp.static_members == ["web-srv-01", "web-srv-02"]
        assert grp.location.name == "shared"
        assert len(sess.staging) == 1
        # The members were consumed into the group: the selection is spent.
        assert sess.selection == []
        assert not isinstance(app.screen, NewGroupScreen)


@pytest.mark.asyncio
async def test_n_carries_description_and_tags(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        await _select(pilot, app, "db-gw")
        await pilot.press("N")
        await pilot.pause()
        app.screen.query_one("#group-new-name", Input).value = "db-pool"
        app.screen.query_one("#group-new-description", Input).value = "the db tier"
        app.screen.query_one("#group-new-tags", Input).value = "prod, core"
        await pilot.press("ctrl+y")
        await pilot.pause()

        grp = next(g for g in sess.working_snapshot.address_groups if g.name == "db-pool")
        assert grp.description == "the db tier"
        assert grp.tags == ["prod", "core"]


@pytest.mark.asyncio
async def test_n_on_an_empty_selection_shows_a_hint(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        _focus_hub(app)
        await pilot.press("N")
        await pilot.pause()
        assert isinstance(app.screen, NewGroupScreen)
        assert app.screen.query_one("#group-new-empty")
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert sess.staging == []


@pytest.mark.asyncio
async def test_an_unnamed_group_does_not_stage(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        await _select(pilot, app, "db-gw")
        await pilot.press("N")
        await pilot.pause()
        await pilot.press("ctrl+y")  # name box left empty
        await pilot.pause()
        assert sess.staging == []
        assert isinstance(app.screen, NewGroupScreen)


@pytest.mark.asyncio
async def test_location_defaults_to_the_narrowest_that_sees_every_member(
    workbench_xml_dg: str,
) -> None:
    # 'dg-only' lives in dg1, so a group holding it belongs in dg1 — shared cannot
    # see into a device-group.
    sess = _session(workbench_xml_dg)
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        await _select(pilot, app, "dg-only")
        await pilot.press("N")
        await pilot.pause()
        assert app.screen.query_one("#group-new-location", Select).value == "dg1"


@pytest.mark.asyncio
async def test_a_device_group_member_blocks_a_shared_group(workbench_xml_dg: str) -> None:
    sess = _session(workbench_xml_dg)
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        await _select(pilot, app, "dg-only")
        await pilot.press("N")
        await pilot.pause()
        screen = app.screen
        screen.query_one("#group-new-name", Input).value = "bad-pool"
        # Override the suggestion: put the group in shared, which cannot see dg1.
        screen.query_one("#group-new-location", Select).value = "shared"
        await pilot.press("ctrl+y")
        await pilot.pause()

        assert sess.staging == []
        assert isinstance(app.screen, NewGroupScreen)  # stays open
        plan = _plain(screen.query_one("#group-new-plan", Static))
        assert "BLOCKED" in plan
        assert "not visible" in plan


@pytest.mark.asyncio
async def test_a_shadowed_member_blocks(workbench_xml_shadow: str) -> None:
    # 'anchor' exists in shared and in dg1. Selecting the shared one and building
    # the group in dg1 would bind the member to dg1's anchor instead.
    sess = _session(workbench_xml_shadow)
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        _focus_hub(app)
        sess.add(SelectionItem(kind="address", name="anchor", location="shared"))
        await pilot.press("N")
        await pilot.pause()
        screen = app.screen
        screen.query_one("#group-new-name", Input).value = "anchors"
        screen.query_one("#group-new-location", Select).value = "dg1"
        await pilot.press("ctrl+y")
        await pilot.pause()

        assert sess.staging == []
        plan = _plain(screen.query_one("#group-new-plan", Static))
        assert "shadow" in plan


@pytest.mark.asyncio
async def test_a_mixed_selection_does_not_stage(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        _focus_hub(app)
        sess.add(SelectionItem(kind="address", name="db-gw", location="shared"))
        sess.add(SelectionItem(kind="service", name="tcp-8443", location="shared"))
        await pilot.press("N")
        await pilot.pause()
        screen = app.screen
        screen.query_one("#group-new-name", Input).value = "nope"
        await pilot.press("ctrl+y")
        await pilot.pause()

        assert sess.staging == []
        assert isinstance(app.screen, NewGroupScreen)
        assert "mixes" in _plain(screen.query_one("#group-new-plan", Static))


@pytest.mark.asyncio
async def test_service_selection_hides_the_description_box(workbench_xml: str) -> None:
    # PAN-OS service-groups have no description field.
    sess = _session(workbench_xml)
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        _focus_hub(app)
        sess.add(SelectionItem(kind="service", name="tcp-8443", location="shared"))
        await pilot.press("N")
        await pilot.pause()
        assert app.screen.query_one("#group-new-description").display is False


@pytest.mark.asyncio
async def test_member_list_survives_textual_markup(workbench_xml: str) -> None:
    # #129: Textual eats `[ ... ]` as a tag, so the member preview must escape the
    # names rather than render an empty "New address-group from".
    sess = _session(workbench_xml)
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        await _select(pilot, app, "web", count=2)
        await pilot.press("N")
        await pilot.pause()
        shown = _plain(app.screen.query_one("#group-new-members", Static))
        assert "web-srv-01" in shown
        assert "web-srv-02" in shown


@pytest.mark.asyncio
async def test_n_is_inert_while_another_spoke_is_open(workbench_xml: str) -> None:
    # The cross-spoke staleness guard: a hub key must not stack a second spoke.
    sess = _session(workbench_xml)
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        _focus_hub(app)
        await pilot.press("c")  # create spoke
        await pilot.pause()
        assert isinstance(app.screen, CreateScreen)
        await pilot.press("N")
        await pilot.pause()
        assert isinstance(app.screen, CreateScreen)  # still the create spoke
