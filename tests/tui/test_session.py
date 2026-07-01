from __future__ import annotations

import pytest

from psc.core.changeset import ChangeSet
from psc.core.dedup import ObjectRef, plan_merge
from psc.core.refs import ReferenceGraph
from psc.core.source import OfflineSource
from psc.output.errors import PscError
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


def test_search_empty_returns_nothing(workbench_xml):
    sess = _session(workbench_xml)
    assert sess.search("") == []
    assert sess.search("   ") == []


def test_toggle_adds_then_removes(workbench_xml):
    sess = _session(workbench_xml)
    item = SelectionItem(kind="address", name="web-srv-01", location="shared")
    assert sess.toggle(item) is True  # added
    assert sess.selection == [item]
    assert sess.toggle(item) is False  # removed
    assert sess.selection == []


def test_toggle_is_idempotent_on_key(workbench_xml):
    sess = _session(workbench_xml)
    a = SelectionItem(kind="address", name="web-srv-01", location="shared")
    a2 = SelectionItem(kind="address", name="web-srv-01", location="shared")
    sess.toggle(a)
    sess.toggle(a2)  # same key -> removes
    assert sess.selection == []


def test_selected_of_kinds_filters(workbench_xml):
    sess = _session(workbench_xml)
    sess.toggle(SelectionItem(kind="address", name="web-srv-01", location="shared"))
    sess.toggle(SelectionItem(kind="service", name="tcp-8443", location="shared"))
    addrs = sess.selected_of_kinds({"address"})
    assert [i.name for i in addrs] == ["web-srv-01"]


def test_clear_selection(workbench_xml):
    sess = _session(workbench_xml)
    sess.toggle(SelectionItem(kind="address", name="db-gw", location="shared"))
    sess.clear_selection()
    assert sess.selection == []


def _merge_web_dupes_cs(sess: WorkbenchSession) -> ChangeSet:
    snap = sess.working_snapshot
    graph = ReferenceGraph.build(snap)
    return plan_merge(
        snap,
        graph,
        keep=ObjectRef(name="web-srv-01", location="shared"),
        drop=ObjectRef(name="web-srv-02", location="shared"),
    )


def test_stage_appends_and_compounds_working_snapshot(workbench_xml):
    sess = _session(workbench_xml)
    assert any(a.name == "web-srv-02" for a in sess.working_snapshot.addresses)
    cs = _merge_web_dupes_cs(sess)
    sess.stage("merge web dupes", cs)
    assert [s.label for s in sess.staging] == ["merge web dupes"]
    assert not any(a.name == "web-srv-02" for a in sess.working_snapshot.addresses)
    assert any(a.name == "web-srv-01" for a in sess.working_snapshot.addresses)


def test_stage_reconciles_selection_dropping_dead_items(workbench_xml):
    sess = _session(workbench_xml)
    keep = SelectionItem(kind="address", name="web-srv-01", location="shared")
    drop = SelectionItem(kind="address", name="web-srv-02", location="shared")
    sess.toggle(keep)
    sess.toggle(drop)
    sess.stage("merge web dupes", _merge_web_dupes_cs(sess))
    assert sess.selection == [keep]


def test_stage_refuses_blocked_changeset(workbench_xml):
    sess = _session(workbench_xml)
    original_xml = sess.working_xml
    cs = ChangeSet(title="bad", blockers=["cannot repoint cross-scope reference"])
    with pytest.raises(PscError):
        sess.stage("bad", cs)
    assert sess.staging == []
    assert sess.working_xml == original_xml  # blocked stage never mutates config


def test_second_stage_plans_against_compounded_reality(workbench_xml):
    sess = _session(workbench_xml)
    sess.stage("merge web dupes", _merge_web_dupes_cs(sess))
    assert sess.search("web-srv-02") == []


def test_combined_set_script_covers_every_staged_change(workbench_xml):
    sess = _session(workbench_xml)
    sess.stage("merge web dupes", _merge_web_dupes_cs(sess))
    script = sess.combined_set_script()
    assert "delete shared address web-srv-02" in script


def test_apply_batch_set_mode_returns_script_no_write(workbench_xml, tmp_path):
    sess = _session(workbench_xml)
    sess.stage("merge web dupes", _merge_web_dupes_cs(sess))
    out = sess.apply_batch(out_path=None)
    assert isinstance(out, ApplyOutcome)
    assert out.mode is OutputMode.SET
    assert out.ops == 1
    assert "delete shared address web-srv-02" in out.detail


def test_apply_batch_offline_writes_compounded_config(workbench_xml, tmp_path):
    sess = _session(workbench_xml)
    sess.output_mode = OutputMode.OFFLINE_APPLY
    sess.stage("merge web dupes", _merge_web_dupes_cs(sess))
    dest = tmp_path / "out.xml"
    out = sess.apply_batch(out_path=str(dest))
    assert dest.exists()
    assert "web-srv-02" not in dest.read_text()
    assert out.out_path == str(dest)


def test_apply_batch_offline_requires_out_path(workbench_xml):
    sess = _session(workbench_xml)
    sess.output_mode = OutputMode.OFFLINE_APPLY
    sess.stage("merge web dupes", _merge_web_dupes_cs(sess))
    with pytest.raises(PscError):
        sess.apply_batch(out_path=None)


def test_apply_batch_empty_staging_is_noop(workbench_xml):
    sess = _session(workbench_xml)
    out = sess.apply_batch(out_path=None)
    assert out.ops == 0


def test_apply_batch_does_not_clear_staging(workbench_xml):
    sess = _session(workbench_xml)
    sess.stage("merge web dupes", _merge_web_dupes_cs(sess))
    sess.apply_batch(out_path=None)
    assert len(sess.staging) == 1  # intentional: operator decides when to clear
