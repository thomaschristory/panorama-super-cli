from __future__ import annotations

from psc.core.models import Rulebase
from psc.core.source import OfflineSource
from psc.tui.screens.rule import plan_rule_add_member
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode


def _session(workbench_xml_rule: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(workbench_xml_rule), output_mode=OutputMode.SET)


def test_add_member_to_rule_source(workbench_xml_rule: str) -> None:
    sess = _session(workbench_xml_rule)
    cs = plan_rule_add_member(sess, "allow-web", Rulebase.PRE, "source", "db-gw")
    assert not cs.is_blocked
    assert not cs.is_empty


def test_add_present_member_is_empty(workbench_xml_rule: str) -> None:
    sess = _session(workbench_xml_rule)
    # web-srv-01 already in allow-web's source -> idempotent no-op
    cs = plan_rule_add_member(sess, "allow-web", Rulebase.PRE, "source", "web-srv-01")
    assert cs.is_empty
