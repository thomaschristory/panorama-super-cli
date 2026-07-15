"""Unit tests for the pure dedup-planning helper (no running Textual app)."""

from __future__ import annotations

import pytest

from psc.core.changeset import ObjectKind
from psc.core.dedup import ObjectRef
from psc.core.source import OfflineSource
from psc.tui.screens.dedup import (
    plan_selection_bucket,
    promote_destinations,
    selection_bucket,
)
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode, SelectionItem


def _session(xml: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(xml), output_mode=OutputMode.SET)


def test_plan_none_without_duplicate_pair(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    # db-gw is unique; a single address can't form a bucket.
    sess.toggle(SelectionItem(kind="address", name="db-gw", location="shared"))
    assert plan_selection_bucket(sess) is None
    assert selection_bucket(sess) is None


def test_plan_ignores_non_address_kinds(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    sess.toggle(SelectionItem(kind="service", name="tcp-8443", location="shared"))
    assert plan_selection_bucket(sess) is None


def test_two_member_bucket_still_works(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    sess.toggle(SelectionItem(kind="address", name="web-srv-02", location="shared"))
    sess.toggle(SelectionItem(kind="address", name="web-srv-01", location="shared"))
    # No explicit keep -> default survivor is the sorted-first member (web-srv-01).
    plan = plan_selection_bucket(sess)
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
    plan = plan_selection_bucket(sess, keep=keep)
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
    plan = plan_selection_bucket(sess, keep=ObjectRef(name="web-srv-03", location="shared"))
    assert plan is not None
    _label, cs = plan
    assert {d.name for d in cs.deletes} == {"web-srv-01", "web-srv-02"}


def test_stale_selection_items_skipped(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    # A ghost item that isn't in the snapshot must not crash or count toward a bucket.
    sess.toggle(SelectionItem(kind="address", name="does-not-exist", location="shared"))
    sess.toggle(SelectionItem(kind="address", name="web-srv-01", location="shared"))
    sess.toggle(SelectionItem(kind="address", name="web-srv-02", location="shared"))
    plan = plan_selection_bucket(sess)
    assert plan is not None
    _label, cs = plan
    assert {d.name for d in cs.deletes} == {"web-srv-02"}


def test_bucket_lists_members_for_the_select(workbench_xml_triple: str) -> None:
    sess = _session(workbench_xml_triple)
    for name in ("web-srv-03", "web-srv-01", "web-srv-02"):
        sess.toggle(SelectionItem(kind="address", name=name, location="shared"))
    found = selection_bucket(sess)
    assert found is not None
    kind, members = found
    assert kind is ObjectKind.ADDRESS
    # Members deterministically ordered (sorted by location, name).
    assert [m.name for m in members] == ["web-srv-01", "web-srv-02", "web-srv-03"]


# --- promote mode (#154): the destination Select as mode switch -------------

# Two sibling device-groups each independently defining the same address value
# (no copy in `shared`) — the case `plan_merge_bucket` cannot fix (there is no
# bucket member visible to *both* device-groups' own referrers) and `promote`
# was built for. Siblings also mean their only common ancestor is `shared`,
# which is what proves `promote_destinations` does not leak a sibling/child DG.
_TWO_DG_DUP_XML = """<?xml version="1.0"?>
<config>
  <shared>
    <address>
      <entry name="anchor"><ip-netmask>10.1.1.1/32</ip-netmask></entry>
    </address>
  </shared>
  <devices>
    <entry name="localhost.localdomain">
      <device-group>
        <entry name="dg1">
          <address>
            <entry name="dg-only"><ip-netmask>10.2.2.2/32</ip-netmask></entry>
          </address>
        </entry>
        <entry name="dg2">
          <address>
            <entry name="dg-only"><ip-netmask>10.2.2.2/32</ip-netmask></entry>
          </address>
        </entry>
      </device-group>
    </entry>
  </devices>
</config>
"""


@pytest.fixture
def session_with_dup_addresses(tmp_path) -> WorkbenchSession:
    p = tmp_path / "config_two_dg_dup.xml"
    p.write_text(_TWO_DG_DUP_XML, encoding="utf-8")
    sess = _session(str(p))
    sess.add(SelectionItem(kind="address", name="dg-only", location="dg1"))
    sess.add(SelectionItem(kind="address", name="dg-only", location="dg2"))
    return sess


@pytest.fixture
def session_with_addr_and_service(workbench_xml: str) -> WorkbenchSession:
    sess = _session(workbench_xml)
    sess.add(SelectionItem(kind="address", name="web-srv-01", location="shared"))
    sess.add(SelectionItem(kind="service", name="tcp-8443", location="shared"))
    return sess


def test_selection_bucket_now_carries_its_kind(
    session_with_dup_addresses: WorkbenchSession,
) -> None:
    found = selection_bucket(session_with_dup_addresses)
    assert found is not None
    kind, members = found
    assert kind is ObjectKind.ADDRESS
    assert len(members) >= 2


def test_heterogeneous_selection_has_no_bucket(
    session_with_addr_and_service: WorkbenchSession,
) -> None:
    assert selection_bucket(session_with_addr_and_service) is None


def test_blank_destination_still_plans_a_merge(
    session_with_dup_addresses: WorkbenchSession,
) -> None:
    label, cs = plan_selection_bucket(session_with_dup_addresses)
    assert label.startswith("merge ")
    assert cs.deletes  # a merge drops the non-survivors


def test_a_destination_plans_a_promote(session_with_dup_addresses: WorkbenchSession) -> None:
    label, cs = plan_selection_bucket(session_with_dup_addresses, dest_name="shared")
    assert label.startswith("promote ")
    assert [u.location for u in cs.upserts] == ["shared"]
    assert cs.reference_edits == []  # upward promotion needs none


def test_promote_destinations_offer_only_common_ancestors(
    session_with_dup_addresses: WorkbenchSession,
) -> None:
    found = selection_bucket(session_with_dup_addresses)
    assert found is not None
    dests = promote_destinations(session_with_dup_addresses, found[1])
    assert dests[0] == "shared"
    # No sibling / child device-group may be offered.
    assert all(
        d == "shared" or d in session_with_dup_addresses.working_snapshot.device_groups
        for d in dests
    )


def test_staging_a_promote_compounds_into_the_working_snapshot(
    session_with_dup_addresses: WorkbenchSession,
) -> None:
    session = session_with_dup_addresses
    label, cs = plan_selection_bucket(session, dest_name="shared")
    session.stage(label, cs)

    locs = {(a.name, a.location.name) for a in session.working_snapshot.addresses}
    assert any(loc == "shared" for _n, loc in locs)


# --- promote mode: the survivor Select doubles as --keep (#154) -------------


def test_the_survivor_select_supplies_keep_name_when_promoting(
    session_with_divergent_dups: WorkbenchSession,
) -> None:
    found = selection_bucket(session_with_divergent_dups)
    assert found is not None
    _kind, members = found
    keep = next(m for m in members if m.name == "h-web1")

    label, cs = plan_selection_bucket(session_with_divergent_dups, keep=keep, dest_name="shared")
    assert label.startswith("promote ")
    assert not cs.is_blocked
    assert cs.upserts[0].name == "h-web1"
    assert cs.reference_edits  # the odd-named copy's referrers are rewritten


def test_promoting_divergent_names_without_a_keep_is_blocked(
    session_with_divergent_dups: WorkbenchSession,
) -> None:
    _label, cs = plan_selection_bucket(session_with_divergent_dups, dest_name="shared")
    assert cs.is_blocked


# --- address-group buckets + cascade (#154 phase 3) -------------------------


def test_address_group_selections_bucket_too(session_with_dup_groups: WorkbenchSession) -> None:
    found = selection_bucket(session_with_dup_groups)
    assert found is not None
    assert found[0] is ObjectKind.ADDRESS_GROUP


def test_cascade_flag_reaches_the_engine(session_with_dup_groups: WorkbenchSession) -> None:
    _label, blocked = plan_selection_bucket(session_with_dup_groups, dest_name="shared")
    assert blocked.is_blocked  # DG-local members, no cascade

    _label, cs = plan_selection_bucket(session_with_dup_groups, dest_name="shared", cascade=True)
    assert not cs.is_blocked
    assert any(u.kind is ObjectKind.ADDRESS for u in cs.upserts)  # the leaves came too
