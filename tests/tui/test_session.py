from __future__ import annotations

import pytest

from psc.core.changeset import ChangeSet, ObjectKind, ObjectUpsert
from psc.core.dedup import ObjectRef, plan_merge
from psc.core.parse import parse_config
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


def test_remove_at_drops_one_selection_entry(workbench_xml):
    sess = _session(workbench_xml)
    a = SelectionItem(kind="address", name="web-srv-01", location="shared")
    b = SelectionItem(kind="address", name="db-gw", location="shared")
    sess.toggle(a)
    sess.toggle(b)
    assert sess.remove_at(0) is True
    assert sess.selection == [b]  # only the chosen entry dropped, the rest kept
    assert sess.remove_at(5) is False  # out-of-range is a no-op


def test_apply_batch_set_mode_writes_script_to_file(workbench_xml, tmp_path):
    sess = _session(workbench_xml)  # OutputMode.SET
    sess.stage("merge web dupes", _merge_web_dupes_cs(sess))
    out = tmp_path / "batch.set"
    expected = sess.combined_set_script()
    outcome = sess.apply_batch(out_path=str(out))
    assert outcome.out_path == str(out)
    text = out.read_text(encoding="utf-8")
    assert text == expected + "\n"  # the full script, newline-terminated
    assert expected  # non-empty (the merge rendered at least one line)
    # SET is an export/preview, not a commit — staging is retained.
    assert len(sess.staging) == 1


def test_apply_batch_set_mode_no_out_path_is_preview(workbench_xml):
    sess = _session(workbench_xml)
    sess.stage("merge web dupes", _merge_web_dupes_cs(sess))
    outcome = sess.apply_batch(out_path=None)
    assert outcome.out_path is None
    assert outcome.detail == sess.combined_set_script()  # script returned inline


def test_apply_batch_set_mode_refuses_source_path(workbench_xml):
    sess = _session(workbench_xml)
    sess.stage("merge web dupes", _merge_web_dupes_cs(sess))
    with pytest.raises(PscError):
        sess.apply_batch(out_path=workbench_xml)  # would clobber the source config


def _add_host_cs() -> ChangeSet:
    return ChangeSet(
        title="add host",
        upserts=[
            ObjectUpsert(
                kind=ObjectKind.ADDRESS,
                name="new-host",
                location="shared",
                fields={"ip-netmask": "10.9.9.9/32"},
            )
        ],
    )


def test_apply_batch_offline_partial_writes_smaller_config(workbench_xml, tmp_path):
    sess = _session(workbench_xml)
    sess.output_mode = OutputMode.OFFLINE_APPLY
    sess.offline_partial = True
    sess.stage("add host", _add_host_cs())
    dest = tmp_path / "partial.xml"
    out = sess.apply_batch(out_path=str(dest))
    assert dest.exists()
    partial = dest.read_text()
    psnap = parse_config(partial)
    # ONLY the touched object is present — untouched siblings are excluded.
    assert [a.name for a in psnap.addresses] == ["new-host"]
    assert "db-gw" not in partial  # sibling not dragged along
    # The partial is much smaller than the full-config rewrite of the same batch.
    full = _full_offline_reference(workbench_xml)
    assert len(partial) < len(full)
    assert out.out_path == str(dest)


def _full_offline_reference(workbench_xml) -> str:
    """Render the FULL compounded config for the same single-add batch, to
    compare sizes against the partial."""
    sess = _session(workbench_xml)
    sess.output_mode = OutputMode.OFFLINE_APPLY
    sess.stage("add host", _add_host_cs())
    return sess.working_xml


def test_apply_batch_offline_full_is_default(workbench_xml, tmp_path):
    sess = _session(workbench_xml)
    sess.output_mode = OutputMode.OFFLINE_APPLY  # offline_partial defaults False
    sess.stage("add host", _add_host_cs())
    dest = tmp_path / "full.xml"
    sess.apply_batch(out_path=str(dest))
    # Default writes the whole compounded config: an untouched sibling is present.
    assert "db-gw" in dest.read_text()


def _add_addr_cs(name: str, ip: str) -> ChangeSet:
    return ChangeSet(
        title=f"add {name}",
        upserts=[
            ObjectUpsert(
                kind=ObjectKind.ADDRESS,
                name=name,
                location="shared",
                fields={"ip-netmask": ip},
            )
        ],
    )


def test_drop_staged_removes_only_that_change_and_rebuilds(workbench_xml):
    sess = _session(workbench_xml)
    sess.stage("add h0", _add_addr_cs("h0", "10.0.0.0/32"))
    sess.stage("add h1", _add_addr_cs("h1", "10.0.0.1/32"))
    sess.stage("add h2", _add_addr_cs("h2", "10.0.0.2/32"))

    sess.drop_staged(1)

    assert [s.label for s in sess.staging] == ["add h0", "add h2"]
    names = {a.name for a in sess.working_snapshot.addresses}
    # Changes 0 and 2 remain applied; the dropped change's object is gone.
    assert "h0" in names
    assert "h2" in names
    assert "h1" not in names


def test_drop_staged_reconciles_selection(workbench_xml):
    sess = _session(workbench_xml)
    sess.stage("add h0", _add_addr_cs("h0", "10.0.0.0/32"))
    sess.stage("add h1", _add_addr_cs("h1", "10.0.0.1/32"))
    keep = SelectionItem(kind="address", name="h0", location="shared")
    doomed = SelectionItem(kind="address", name="h1", location="shared")
    sess.toggle(keep)
    sess.toggle(doomed)

    sess.drop_staged(0)  # drop h0's creation

    # h0 is now gone from the working snapshot, so its selection entry is dropped;
    # h1 (still created by the surviving change) stays selected.
    assert sess.selection == [doomed]


def test_drop_staged_out_of_range_is_noop(workbench_xml):
    sess = _session(workbench_xml)
    sess.stage("add h0", _add_addr_cs("h0", "10.0.0.0/32"))
    before_xml = sess.working_xml
    sess.drop_staged(5)
    assert len(sess.staging) == 1
    assert sess.working_xml == before_xml


def test_drop_staged_dependency_failure_keeps_batch_intact(workbench_xml):
    sess = _session(workbench_xml)
    sess.stage("add h0", _add_addr_cs("h0", "10.0.0.0/32"))
    sess.stage("add h1", _add_addr_cs("h1", "10.0.0.1/32"))
    # A third change that only applies cleanly on top of the others: it upserts
    # into a device-group scope that does not exist in this config, so replaying
    # it during a rebuild raises. It is inserted directly (bypassing stage(),
    # which would refuse it) to model a batch whose later change depends on an
    # earlier one — dropping change 0 must not leave a half-rebuilt state.
    dependent = ChangeSet(
        title="add into dg",
        upserts=[
            ObjectUpsert(
                kind=ObjectKind.ADDRESS,
                name="dep",
                location="ghost-dg",
                fields={"ip-netmask": "10.0.0.9/32"},
            )
        ],
    )
    sess.staging.append(StagedChange(label="dependent", changeset=dependent))
    staging_before = list(sess.staging)
    xml_before = sess.working_xml
    snap_names_before = {a.name for a in sess.working_snapshot.addresses}

    with pytest.raises(PscError):
        sess.drop_staged(0)

    # Nothing changed: the whole batch and working state survive intact.
    assert sess.staging == staging_before
    assert sess.working_xml == xml_before
    assert {a.name for a in sess.working_snapshot.addresses} == snap_names_before
