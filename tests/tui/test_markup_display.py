"""Regression tests for #129: the TUI must not swallow `set`-script member lists.

Textual's console-markup engine treats ``[ ... ]`` as a tag, so a rendered
`set ... destination [ addr-a addr-b ]` line was displayed as an empty
``... destination`` with the members eaten — a display-only bug (the emitted
script/file was always correct). These tests assert the members survive after
Textual parses the markup.
"""

from __future__ import annotations

import pytest
from textual.content import Content
from textual.markup import to_content
from textual.widgets import DataTable, Static

from psc.core.dedup import ObjectRef, plan_merge
from psc.core.parse import parse_config
from psc.core.refs import ReferenceGraph
from psc.core.source import OfflineSource
from psc.tui.app import WorkbenchApp
from psc.tui.screens.staged import staged_detail
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode
from psc.tui.widgets.review import review_lines

# Two duplicate addresses; a device-group rule's destination references the one
# that gets dropped, so the merge rewrites the field to a 2+ member list — the
# exact `[ ... ]` shape Textual markup would otherwise eat.
_RULE_DUP_XML = """<?xml version="1.0"?>
<config>
  <shared>
    <address>
      <entry name="XXX-3"><ip-netmask>10.0.0.5/32</ip-netmask></entry>
      <entry name="XXX-4"><ip-netmask>10.0.0.5/32</ip-netmask></entry>
      <entry name="OTHER"><ip-netmask>10.0.0.9/32</ip-netmask></entry>
    </address>
  </shared>
  <devices><entry name="localhost.localdomain"><device-group>
    <entry name="DGP-NYC"><pre-rulebase><security><rules>
      <entry name="ALLOW-NYC-x">
        <from><member>any</member></from><to><member>any</member></to>
        <source><member>any</member></source>
        <destination><member>XXX-3</member><member>OTHER</member></destination>
        <service><member>any</member></service>
        <application><member>any</member></application>
        <action>allow</action>
      </entry>
    </rules></security></pre-rulebase></entry>
  </device-group></entry></devices>
</config>
"""

_EXPECTED = (
    "set device-group DGP-NYC pre-rulebase security rules ALLOW-NYC-x destination [ XXX-4 OTHER ]"
)


def _merge_cs(snapshot):
    graph = ReferenceGraph.build(snapshot)
    return plan_merge(
        snapshot,
        graph,
        keep=ObjectRef(name="XXX-4", location="shared"),
        drop=ObjectRef(name="XXX-3", location="shared"),
    )


def test_review_panel_preserves_set_member_list() -> None:
    cs = _merge_cs(parse_config(_RULE_DUP_XML))
    # What the operator actually sees once Textual parses the markup:
    rendered = "\n".join(to_content(line).plain for line in review_lines(cs))
    assert _EXPECTED in rendered


def test_staged_detail_preserves_set_member_list(tmp_path) -> None:
    p = tmp_path / "c.xml"
    p.write_text(_RULE_DUP_XML, encoding="utf-8")
    session = WorkbenchSession(source=OfflineSource(str(p)), output_mode=OutputMode.SET)
    session.stage("merge", _merge_cs(session.working_snapshot))
    # The staged detail is displayed as plain Content, so nothing is eaten.
    assert _EXPECTED in Content(staged_detail(session, 0)).plain


@pytest.mark.asyncio
async def test_staged_screen_detail_shows_members(tmp_path) -> None:
    p = tmp_path / "c.xml"
    p.write_text(_RULE_DUP_XML, encoding="utf-8")
    session = WorkbenchSession(source=OfflineSource(str(p)), output_mode=OutputMode.SET)
    session.stage("merge", _merge_cs(session.working_snapshot))
    app = WorkbenchApp(session)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("s")  # staged changelist
        await pilot.pause()
        detail = app.screen.query_one("#staged-detail", Static)
        rendered = detail.render()  # the Content actually shown on screen
        plain = rendered.plain if hasattr(rendered, "plain") else str(rendered)
        assert _EXPECTED in plain
