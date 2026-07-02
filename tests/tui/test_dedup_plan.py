"""Unit tests for the pure dedup-planning helper (no running Textual app)."""

from __future__ import annotations

from psc.core.dedup import ObjectRef
from psc.core.source import OfflineSource
from psc.tui.screens.dedup import plan_selection_bucket_merge, selection_bucket
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode, SelectionItem


def _session(xml: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(xml), output_mode=OutputMode.SET)


def test_plan_none_without_duplicate_pair(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    # db-gw is unique; a single address can't form a bucket.
    sess.toggle(SelectionItem(kind="address", name="db-gw", location="shared"))
    assert plan_selection_bucket_merge(sess) is None
    assert selection_bucket(sess) is None


def test_plan_ignores_non_address_kinds(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    sess.toggle(SelectionItem(kind="service", name="tcp-8443", location="shared"))
    assert plan_selection_bucket_merge(sess) is None


def test_two_member_bucket_still_works(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    sess.toggle(SelectionItem(kind="address", name="web-srv-02", location="shared"))
    sess.toggle(SelectionItem(kind="address", name="web-srv-01", location="shared"))
    # No explicit keep -> default survivor is the sorted-first member (web-srv-01).
    plan = plan_selection_bucket_merge(sess)
    assert plan is not None
    label, cs = plan
    assert not cs.is_blocked
    assert "web-srv-01" in label
    # exactly one object dropped
    assert {d.name for d in cs.deletes} == {"web-srv-02"}


def test_three_members_collapse_toward_chosen_survivor(workbench_xml_triple: str) -> None:
    sess = _session(workbench_xml_triple)
    for name in ("web-srv-01", "web-srv-02", "web-srv-03"):
        sess.toggle(SelectionItem(kind="address", name=name, location="shared"))
    keep = ObjectRef(name="web-srv-02", location="shared")
    plan = plan_selection_bucket_merge(sess, keep=keep)
    assert plan is not None
    _label, cs = plan
    assert not cs.is_blocked
    # The two non-survivors are dropped in ONE changeset...
    assert {d.name for d in cs.deletes} == {"web-srv-01", "web-srv-03"}
    # ...and the group referencing all three is repointed onto the survivor.
    pool = next(e for e in cs.reference_edits if e.referrer_name == "web-pool")
    assert pool.after == ["web-srv-02"]


def test_choosing_a_different_survivor_changes_the_kept(workbench_xml_triple: str) -> None:
    sess = _session(workbench_xml_triple)
    for name in ("web-srv-01", "web-srv-02", "web-srv-03"):
        sess.toggle(SelectionItem(kind="address", name=name, location="shared"))
    plan = plan_selection_bucket_merge(sess, keep=ObjectRef(name="web-srv-03", location="shared"))
    assert plan is not None
    _label, cs = plan
    assert {d.name for d in cs.deletes} == {"web-srv-01", "web-srv-02"}


def test_stale_selection_items_skipped(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    # A ghost item that isn't in the snapshot must not crash or count toward a bucket.
    sess.toggle(SelectionItem(kind="address", name="does-not-exist", location="shared"))
    sess.toggle(SelectionItem(kind="address", name="web-srv-01", location="shared"))
    sess.toggle(SelectionItem(kind="address", name="web-srv-02", location="shared"))
    plan = plan_selection_bucket_merge(sess)
    assert plan is not None
    _label, cs = plan
    assert {d.name for d in cs.deletes} == {"web-srv-02"}


def test_bucket_lists_members_for_the_select(workbench_xml_triple: str) -> None:
    sess = _session(workbench_xml_triple)
    for name in ("web-srv-03", "web-srv-01", "web-srv-02"):
        sess.toggle(SelectionItem(kind="address", name=name, location="shared"))
    bucket = selection_bucket(sess)
    assert bucket is not None
    # Members deterministically ordered (sorted by location, name).
    assert [m.name for m in bucket] == ["web-srv-01", "web-srv-02", "web-srv-03"]
