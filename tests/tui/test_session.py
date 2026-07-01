from __future__ import annotations

from psc.core.changeset import ChangeSet
from psc.core.source import OfflineSource
from psc.tui.session import WorkbenchSession
from psc.tui.state import ApplyOutcome, OutputMode, SelectionItem, StagedChange


def test_selection_item_key_is_kind_name_location():
    item = SelectionItem(kind="address", name="web-srv-01", location="shared")
    assert item.key == ("address", "web-srv-01", "shared")


def test_staged_change_holds_label_and_changeset():
    cs = ChangeSet(title="merge")
    staged = StagedChange(label="merge web dupes", changeset=cs)
    assert staged.label == "merge web dupes"
    assert staged.changeset is cs


def test_output_mode_values():
    assert OutputMode.SET.value == "set"
    assert OutputMode.OFFLINE_APPLY.value == "offline-apply"
    assert OutputMode.LIVE_APPLY.value == "live-apply"


def test_apply_outcome_fields():
    out = ApplyOutcome(mode=OutputMode.SET, ops=3, out_path=None, detail="script")
    assert out.ops == 3 and out.detail == "script"


def _session(workbench_xml) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(workbench_xml), output_mode=OutputMode.SET)


def test_search_by_name_substring_mixes_kinds(workbench_xml):
    sess = _session(workbench_xml)
    hits = sess.search("srv")
    names = {h.name for h in hits}
    assert names == {"web-srv-01", "web-srv-02"}
    assert all(h.kind == "address" for h in hits)


def test_search_by_ip_finds_both_duplicates(workbench_xml):
    sess = _session(workbench_xml)
    hits = sess.search("10.0.5.10")
    names = {h.name for h in hits}
    assert {"web-srv-01", "web-srv-02"} <= names


def test_search_service_by_name(workbench_xml):
    sess = _session(workbench_xml)
    hits = sess.search("tcp-8443")
    assert [h.kind for h in hits] == ["service"]


def test_search_is_deduped(workbench_xml):
    sess = _session(workbench_xml)
    hits = sess.search("10.0.5.10")
    keys = [h.key for h in hits]
    assert len(keys) == len(set(keys))
