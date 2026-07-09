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


def _all_labels(tree: Tree) -> list[str]:  # type: ignore[type-arg]
    out: list[str] = []
    stack = list(tree.root.children)
    while stack:
        node = stack.pop()
        out.append(str(node.label))
        stack.extend(node.children)
    return out
