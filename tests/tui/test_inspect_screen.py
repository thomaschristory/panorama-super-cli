from __future__ import annotations

import pytest
from textual.widgets import DataTable, Input, Tree

from psc.core.source import OfflineSource
from psc.tui.app import WorkbenchApp
from psc.tui.screens.inspect import InspectScreen, inspect_object_for
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode, SelectionItem


def _session(path: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(path), output_mode=OutputMode.SET)


def test_inspect_object_for_expands_group(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    item = SelectionItem(kind="address-group", name="web-pool", location="shared")
    (view,) = inspect_object_for(sess, item)
    assert view.kind == "address-group"
    assert view.effective_leaves == ["10.0.5.10/32"]
    assert {c.name for c in view.tree.children} == {"web-srv-01"}


@pytest.mark.asyncio
async def test_v_opens_inspect_screen(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "web-pool"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("v")
        await pilot.pause()
        assert isinstance(app.screen, InspectScreen)
        tree = app.screen.query_one("#inspect-tree", Tree)
        # root -> the object view -> its member
        labels = _all_labels(tree)
        assert any("web-srv-01" in label for label in labels)


@pytest.mark.asyncio
async def test_inspect_is_read_only_and_pops(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "web-pool"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("v")
        await pilot.pause()
        assert isinstance(app.screen, InspectScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, InspectScreen)
        # Inspecting never touches the selection or staging.
        assert sess.selection == []
        assert sess.staging == []


_NESTED_XML = """<?xml version="1.0"?>
<config><shared>
  <address>
    <entry name="a1"><ip-netmask>10.0.0.1/32</ip-netmask></entry>
    <entry name="a2"><ip-netmask>10.0.0.2/32</ip-netmask></entry>
  </address>
  <address-group>
    <entry name="inner"><static><member>a2</member></static></entry>
    <entry name="outer"><static>
      <member>a1</member><member>inner</member>
    </static></entry>
  </address-group>
</shared></config>
"""


@pytest.mark.asyncio
async def test_nested_group_starts_collapsed_and_leaves_have_no_arrow(tmp_path) -> None:  # type: ignore[no-untyped-def]
    p = tmp_path / "nested.xml"
    p.write_text(_NESTED_XML, encoding="utf-8")
    sess = _session(str(p))
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "outer"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("v")
        await pilot.pause()
        tree = app.screen.query_one("#inspect-tree", Tree)
        # A single match: the object IS the root (no redundant heading), expanded
        # to show its direct members.
        assert "outer" in str(tree.root.label)
        assert tree.root.is_expanded
        by_label = {str(c.label): c for c in tree.root.children}
        # ...the nested group `inner` is present but collapsed (drill in on demand)
        inner = next(c for lbl, c in by_label.items() if "inner" in lbl)
        assert inner.allow_expand and not inner.is_expanded
        # ...and a true leaf carries no expand arrow.
        a1 = next(c for lbl, c in by_label.items() if "a1" in lbl)
        assert not a1.allow_expand


def _all_labels(tree: Tree) -> list[str]:  # type: ignore[type-arg]
    out: list[str] = []
    stack = list(tree.root.children)
    while stack:
        node = stack.pop()
        out.append(str(node.label))
        stack.extend(node.children)
    return out
