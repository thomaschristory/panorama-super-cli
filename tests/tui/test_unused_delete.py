"""Workbench tests for the unused → delete workflow (issue #181).

The unused spoke was a dead end: it listed candidates and offered no way to act
on them. It now pushes rows into the selection buffer, and the delete spoke
turns that selection into a staged, reference-safe plan.

The spokes stay separate on purpose. The operator always sees a plan between
the candidate list and the change.
"""

from __future__ import annotations

import pytest
from textual.widgets import Checkbox, DataTable

from psc.core.source import OfflineSource
from psc.tui.app import WorkbenchApp
from psc.tui.screens.delete import DeleteScreen, deletable_items, plan_delete
from psc.tui.screens.unused import UnusedScreen, add_rows_to_selection, unused_rows
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode, SelectionItem
from psc.tui.widgets.review import ReviewPanel, review_lines

TAGGED_XML = """<?xml version="1.0"?>
<config>
  <shared>
    <tag><entry name="web"><color>color1</color></entry></tag>
    <address>
      <entry name="h-plain"><ip-netmask>10.0.0.1/32</ip-netmask></entry>
      <entry name="h-tagged">
        <ip-netmask>10.0.0.2/32</ip-netmask>
        <tag><member>web</member></tag>
      </entry>
    </address>
  </shared>
  <devices><entry name="localhost.localdomain"><device-group/></entry></devices>
</config>
"""


@pytest.fixture
def workbench_xml_tagged(tmp_path):
    p = tmp_path / "config_tagged.xml"
    p.write_text(TAGGED_XML, encoding="utf-8")
    return str(p)


def _session(xml_path: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(xml_path), output_mode=OutputMode.SET)


def _app(xml_path: str) -> WorkbenchApp:
    return WorkbenchApp(_session(xml_path))


# -- pure functions ------------------------------------------------------


def test_unused_rows_carry_tags(workbench_xml_tagged: str) -> None:
    rows = {r.name: r.tags for r in unused_rows(_session(workbench_xml_tagged), "address")}
    assert rows["h-tagged"] == ["web"]
    assert rows["h-plain"] == []


def test_unused_row_becomes_a_selection_item(workbench_xml_refs: str) -> None:
    rows = unused_rows(_session(workbench_xml_refs), "address")
    assert rows[0].item == SelectionItem(
        kind=rows[0].kind, name=rows[0].name, location=rows[0].location
    )


def test_add_rows_to_selection_is_idempotent(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    rows = unused_rows(sess, "address")
    assert add_rows_to_selection(sess, rows) == len(rows)
    assert add_rows_to_selection(sess, rows) == 0
    assert len(sess.selection) == len(rows)


def test_plan_delete_covers_every_selected_kind(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    add_rows_to_selection(sess, unused_rows(sess, "address"))
    add_rows_to_selection(sess, unused_rows(sess, "address-group"))
    cs = plan_delete(sess, deletable_items(sess))
    assert not cs.is_blocked, cs.blockers
    names = {d.name for d in cs.deletes}
    assert {"db-gw", "net-10-0-5", "web-pool", "web-srv-01"} <= names


def test_plan_delete_warns_about_a_tagged_candidate(workbench_xml_tagged: str) -> None:
    sess = _session(workbench_xml_tagged)
    add_rows_to_selection(sess, [r for r in unused_rows(sess, "address") if r.tags])
    cs = plan_delete(sess, deletable_items(sess))
    assert any("dynamic address-group" in w for w in cs.warnings)


def test_deletable_items_ignores_nothing_selectable(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    assert deletable_items(sess) == []


# -- the spokes ----------------------------------------------------------


@pytest.mark.asyncio
async def test_unused_table_shows_a_tags_column(workbench_xml_tagged: str) -> None:
    app = _app(workbench_xml_tagged)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("i")
        await pilot.pause()
        table = app.screen.query_one("#unused-table", DataTable)
        assert [str(c.label) for c in table.columns.values()] == [
            "kind",
            "name",
            "location",
            "tags",
        ]


@pytest.mark.asyncio
async def test_space_sends_the_row_under_the_cursor(workbench_xml_refs: str) -> None:
    app = _app(workbench_xml_refs)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("i")
        await pilot.pause()
        assert isinstance(app.screen, UnusedScreen)
        await pilot.press("space")
        await pilot.pause()
        assert len(app.session.selection) == 1


@pytest.mark.asyncio
async def test_a_sends_every_listed_row(workbench_xml_refs: str) -> None:
    app = _app(workbench_xml_refs)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("i")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, UnusedScreen)
        listed = screen.query_one("#unused-table", DataTable).row_count
        await pilot.press("a")
        await pilot.pause()
        assert len(app.session.selection) == listed


@pytest.mark.asyncio
async def test_unused_then_delete_stages_one_plan(workbench_xml_refs: str) -> None:
    app = _app(workbench_xml_refs)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("i")
        await pilot.pause()
        await pilot.press("a")  # every unused address to the selection
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        selected = len(app.session.selection)
        assert selected > 0
        await pilot.press("X")
        await pilot.pause()
        assert isinstance(app.screen, DeleteScreen)
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert len(app.session.staging) == 1
        assert not isinstance(app.screen, DeleteScreen)
        # Staging reconciles the selection: the deleted objects are gone.
        assert len(app.session.selection) < selected


@pytest.mark.asyncio
async def test_delete_spoke_shows_an_empty_state(workbench_xml_refs: str) -> None:
    app = _app(workbench_xml_refs)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("X")
        await pilot.pause()
        assert isinstance(app.screen, DeleteScreen)
        assert app.screen.query_one("#delete-empty")
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert app.session.staging == []


@pytest.mark.asyncio
async def test_unused_spoke_stages_nothing_by_itself(workbench_xml_refs: str) -> None:
    """The candidate list must never mutate the config on its own."""
    app = _app(workbench_xml_refs)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("i")
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert app.session.staging == []


# -- the blocker gate ----------------------------------------------------


BLOCKED_XML = """<?xml version="1.0"?>
<config>
  <shared>
    <tag><entry name="web"><color>color1</color></entry></tag>
    <address>
      <entry name="h-tagged">
        <ip-netmask>10.0.0.2/32</ip-netmask>
        <tag><member>web</member></tag>
      </entry>
    </address>
    <address-group>
      <entry name="dag-web"><dynamic><filter>'web'</filter></dynamic></entry>
    </address-group>
  </shared>
  <devices><entry name="localhost.localdomain"><device-group/></entry></devices>
</config>
"""


@pytest.fixture
def workbench_xml_blocked(tmp_path):
    p = tmp_path / "config_blocked.xml"
    p.write_text(BLOCKED_XML, encoding="utf-8")
    return str(p)


def test_a_surviving_dag_blocks_the_plan(workbench_xml_blocked: str) -> None:
    sess = _session(workbench_xml_blocked)
    sess.add(SelectionItem(kind="address", name="h-tagged", location="shared"))
    cs = plan_delete(sess, deletable_items(sess))
    assert cs.is_blocked
    assert cs.op_count == 0


@pytest.mark.asyncio
async def test_a_blocked_plan_is_never_staged(workbench_xml_blocked: str) -> None:
    app = _app(workbench_xml_blocked)
    app.session.add(SelectionItem(kind="address", name="h-tagged", location="shared"))
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("X")
        await pilot.pause()
        assert isinstance(app.screen, DeleteScreen)
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert app.session.staging == []
        # Stay on the spoke so the operator reads the blocker.
        assert isinstance(app.screen, DeleteScreen)


@pytest.mark.asyncio
async def test_the_review_panel_shows_the_blocker(workbench_xml_blocked: str) -> None:
    app = _app(workbench_xml_blocked)
    app.session.add(SelectionItem(kind="address", name="h-tagged", location="shared"))
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("X")
        await pilot.pause()
        panel = app.screen.query_one("#review", ReviewPanel)
        assert not panel.can_apply


@pytest.mark.asyncio
async def test_the_review_panel_shows_the_candidate_warnings(workbench_xml_tagged: str) -> None:
    app = _app(workbench_xml_tagged)
    app.session.add(SelectionItem(kind="address", name="h-tagged", location="shared"))
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("X")
        await pilot.pause()
        panel = app.screen.query_one("#review", ReviewPanel)
        rendered = "\n".join(review_lines(panel._cs))
        assert "dynamic address-group" in rendered
        assert "is a shared address" in rendered


@pytest.mark.asyncio
async def test_keep_rules_is_read_at_stage_time(workbench_xml_rule: str) -> None:
    """Ticking the box must change the staged plan, not only the preview.

    The selection here is a *used* address — `allow-web` sources it and nothing
    else. Deleting it empties the rule's source, so the box decides whether the
    plan removes the rule.
    """
    app = _app(workbench_xml_rule)
    app.session.add(SelectionItem(kind="address", name="web-srv-01", location="shared"))
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("X")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, DeleteScreen)
        # Unticked, the orphaned rule goes.
        assert plan_delete(app.session, deletable_items(app.session)).rule_deletes
        screen.query_one("#delete-keep-rules", Checkbox).value = True
        await pilot.pause()
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert len(app.session.staging) == 1
        staged = app.session.staging[0].changeset
        assert not staged.rule_deletes
        assert any("keep-rules" in w for w in staged.warnings)
