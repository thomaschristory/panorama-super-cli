"""Unit tests for the four parity-spoke glue functions (Textual-free)."""

from __future__ import annotations

from psc.core.source import OfflineSource
from psc.tui.screens.dangling import dangling_rows
from psc.tui.screens.lint import lint_rows
from psc.tui.screens.name_apply import plan_scheme
from psc.tui.screens.unused import unused_rows
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode


def _session(path: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(path), output_mode=OutputMode.SET)


# net-10-0-5 is defined and referenced by no rule; web-srv-01 is in web-pool but
# web-pool itself is reachable by no rule, so both addresses are unused here.
def test_unused_rows_finds_unused_address(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    rows = unused_rows(sess, "address")
    names = {(r.kind, r.name, r.location) for r in rows}
    assert ("address", "net-10-0-5", "shared") in names
    assert ("address", "db-gw", "shared") in names


def test_unused_rows_empty_kind(workbench_xml_refs: str) -> None:
    # No tags defined -> the tag kind yields no unused rows (and never raises).
    assert unused_rows(_session(workbench_xml_refs), "tag") == []


_DANGLING_XML = """<?xml version="1.0"?>
<config>
  <shared>
    <address>
      <entry name="web-srv-01"><ip-netmask>10.0.5.10/32</ip-netmask></entry>
    </address>
    <address-group>
      <entry name="web-pool">
        <static>
          <member>web-srv-01</member>
          <member>ghost-host</member>
        </static>
      </entry>
    </address-group>
  </shared>
  <devices><entry name="localhost.localdomain"><device-group/></entry></devices>
</config>
"""


def test_dangling_rows_finds_missing_member(tmp_path) -> None:  # type: ignore[no-untyped-def]
    p = tmp_path / "dangling.xml"
    p.write_text(_DANGLING_XML, encoding="utf-8")
    rows = dangling_rows(_session(str(p)))
    assert any(
        r.referrer_kind == "address-group"
        and r.referrer_name == "web-pool"
        and r.target_name == "ghost-host"
        for r in rows
    )


def test_dangling_rows_empty_when_clean(workbench_xml: str) -> None:
    assert dangling_rows(_session(workbench_xml)) == []


# db-gw is an ip-netmask /32 host, so the default scheme suggests H-10.0.9.1;
# it is not compliant with its current name, so lint reports drift for it.
def test_lint_rows_reports_drift(workbench_xml_refs: str) -> None:
    rows = lint_rows(_session(workbench_xml_refs))
    assert rows  # non-compliant findings present
    assert all(not r.compliant for r in rows)
    assert any(r.current == "db-gw" and r.suggested == "H-10.0.9.1" for r in rows)


def test_plan_scheme_renames_noncompliant(workbench_xml_refs: str) -> None:
    cs = plan_scheme(_session(workbench_xml_refs))
    assert not cs.is_blocked
    assert cs.renames  # at least one rename planned
    new_names = {r.new_name for r in cs.renames}
    assert "H-10.0.9.1" in new_names


_SCHEME_COLLISION_XML = """<?xml version="1.0"?>
<config>
  <shared>
    <address>
      <entry name="db-gw"><ip-netmask>10.0.9.1/32</ip-netmask></entry>
      <entry name="H-10.0.9.1"><ip-netmask>10.0.9.9/32</ip-netmask></entry>
    </address>
  </shared>
  <devices><entry name="localhost.localdomain"><device-group/></entry></devices>
</config>
"""


def test_plan_scheme_blocks_on_collision(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # db-gw wants to become H-10.0.9.1, but that name already exists (as a
    # different /32) -> the bulk rename is blocked (collision) and carries no ops.
    p = tmp_path / "collision.xml"
    p.write_text(_SCHEME_COLLISION_XML, encoding="utf-8")
    cs = plan_scheme(_session(str(p)))
    assert cs.is_blocked
    assert not cs.renames
