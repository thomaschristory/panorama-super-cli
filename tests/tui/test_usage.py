from __future__ import annotations

from psc.core.source import OfflineSource
from psc.tui.screens.usage import UsageRow, selection_where_used
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode, SelectionItem


def _session(workbench_xml: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(workbench_xml), output_mode=OutputMode.SET)


def test_where_used_finds_group_referrer(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    sess.toggle(SelectionItem(kind="address", name="web-srv-01", location="shared"))
    rows = selection_where_used(sess)
    assert any(
        isinstance(r, UsageRow)
        and r.object_name == "web-srv-01"
        and r.referrer_kind == "address-group"
        and r.referrer_name == "web-pool"
        for r in rows
    )


def test_where_used_empty_for_unreferenced(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    sess.toggle(SelectionItem(kind="address", name="db-gw", location="shared"))
    assert selection_where_used(sess) == []


def test_where_used_only_considers_selection(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    assert selection_where_used(sess) == []


_TWO_OWNERS_XML = """<?xml version="1.0"?>
<config><shared>
  <address>
    <entry name="a1"><ip-netmask>10.0.0.1/32</ip-netmask></entry>
    <entry name="a2"><ip-netmask>10.0.0.2/32</ip-netmask></entry>
  </address>
  <address-group>
    <entry name="g1"><static><member>a1</member></static></entry>
    <entry name="g2"><static><member>a2</member></static></entry>
  </address-group>
</shared></config>
"""


def test_where_used_lists_all_selected_with_owner_and_location(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # Two selected objects, each referenced by a different group: every selected
    # object's usage is listed and each row is attributed to its OWNING object,
    # location included, so same-named objects at different scopes stay distinct (#86).
    p = tmp_path / "two.xml"
    p.write_text(_TWO_OWNERS_XML, encoding="utf-8")
    sess = _session(str(p))
    sess.toggle(SelectionItem(kind="address", name="a1", location="shared"))
    sess.toggle(SelectionItem(kind="address", name="a2", location="shared"))
    rows = selection_where_used(sess)
    owners = {(r.object_name, r.object_location, r.referrer_name) for r in rows}
    assert ("a1", "shared", "g1") in owners
    assert ("a2", "shared", "g2") in owners
    assert all(r.object_location for r in rows)  # owner location always populated
