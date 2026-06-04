"""Tests for the shared unmappable-reference gate (issue #28).

The gate is the single source of truth for "can both appliers express this
reference edit as a flat member rewrite?" When the answer is no *and* the same
plan tears the target down (delete/rename), the silently-skipped repoint would
leave a dangling reference — so it must become a hard blocker, not a warning.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from psc.core.apply_live import plan_xapi_ops
from psc.core.apply_xml import apply_changeset
from psc.core.changeset import (
    ChangeSet,
    ObjectDelete,
    ObjectKind,
    ObjectRename,
    ReferenceEdit,
    gate_unmappable_reference_edits,
    reference_edit_is_mappable,
)
from psc.core.models import Snapshot
from psc.core.naming import plan_rename
from psc.core.refs import ReferenceGraph
from psc.output.errors import ErrorType, PscError


def _edit(**kw: object) -> ReferenceEdit:
    base: dict[str, object] = {
        "referrer_kind": "address-group",
        "referrer_name": "g",
        "referrer_location": "shared",
        "field": "static",
    }
    base.update(kw)
    return ReferenceEdit(**base)  # type: ignore[arg-type]


def test_flat_member_fields_are_mappable() -> None:
    assert reference_edit_is_mappable(_edit(referrer_kind="address-group", field="static"))
    assert reference_edit_is_mappable(_edit(referrer_kind="service-group", field="members"))


def test_rule_field_is_mappable_only_with_rulebase() -> None:
    assert reference_edit_is_mappable(
        _edit(referrer_kind="security-rule", field="destination", rulebase="pre")
    )
    assert reference_edit_is_mappable(
        _edit(referrer_kind="nat-rule", field="source", rulebase="pre")
    )
    # trigger 2: a rule edit with no rulebase can't be addressed by either applier
    assert not reference_edit_is_mappable(
        _edit(referrer_kind="security-rule", field="destination", rulebase=None)
    )
    assert not reference_edit_is_mappable(
        _edit(referrer_kind="nat-rule", field="source", rulebase=None)
    )


def test_nat_translation_and_nonmember_fields_are_unmappable() -> None:
    # trigger 1: NAT translation fields are nested, not flat member lists
    assert not reference_edit_is_mappable(
        _edit(referrer_kind="nat-rule", field="source-translation", rulebase="pre")
    )
    assert not reference_edit_is_mappable(
        _edit(referrer_kind="nat-rule", field="destination-translation", rulebase="pre")
    )
    # a nat-rule `service` is a scalar, not a member list either
    assert not reference_edit_is_mappable(
        _edit(referrer_kind="nat-rule", field="service", rulebase="pre")
    )


def test_gate_blocks_unmappable_edit_when_plan_deletes_target() -> None:
    cs = ChangeSet(
        title="merge",
        reference_edits=[
            _edit(
                referrer_kind="nat-rule",
                referrer_name="nat-web",
                referrer_location="shared",
                field="source-translation",
                rulebase="pre",
            )
        ],
        deletes=[ObjectDelete(kind=ObjectKind.ADDRESS, name="net-10", location="shared")],
    )
    gate_unmappable_reference_edits(cs)
    assert cs.is_blocked
    blocker = " ".join(cs.blockers)
    assert "net-10" in blocker  # names the torn-down object
    assert "nat-web" in blocker  # names the unmappable referrer
    assert "source-translation" in blocker


def test_gate_blocks_unmappable_edit_when_plan_renames_target() -> None:
    cs = ChangeSet(
        title="rename",
        reference_edits=[
            _edit(
                referrer_kind="security-rule",
                referrer_name="r1",
                referrer_location="shared",
                field="destination",
                rulebase=None,
            )
        ],
        renames=[
            ObjectRename(
                kind=ObjectKind.ADDRESS, location="shared", old_name="net-10", new_name="N-10"
            )
        ],
    )
    gate_unmappable_reference_edits(cs)
    assert cs.is_blocked
    assert any("net-10" in b and "r1" in b for b in cs.blockers)


def test_gate_warns_but_passes_when_no_teardown() -> None:
    cs = ChangeSet(
        title="advisory",
        reference_edits=[
            _edit(
                referrer_kind="nat-rule",
                referrer_name="nat-web",
                referrer_location="shared",
                field="source-translation",
                rulebase="pre",
            )
        ],
    )
    gate_unmappable_reference_edits(cs)
    assert not cs.is_blocked
    assert any("nat-web" in w for w in cs.warnings)


def test_gate_noop_when_all_edits_mappable() -> None:
    cs = ChangeSet(
        title="clean",
        reference_edits=[_edit(referrer_kind="address-group", field="static")],
        deletes=[ObjectDelete(kind=ObjectKind.ADDRESS, name="a", location="shared")],
    )
    gate_unmappable_reference_edits(cs)
    assert not cs.is_blocked
    assert not cs.warnings


def test_engine_blocked_plan_refused_on_both_paths(snapshot: Snapshot, fixture_path: Path) -> None:
    """The gate-produced blocker is honored identically offline and live: both
    appliers refuse with CONFLICT (exit 6) before mutating anything (#28)."""
    graph = ReferenceGraph.build(snapshot)
    cs = plan_rename(
        snapshot,
        graph,
        kind=ObjectKind.ADDRESS,
        location_name="shared",
        old_name="net-10",
        new_name="N-10.0.0.0_24",
    )
    assert cs.is_blocked

    with pytest.raises(PscError) as offline:
        apply_changeset(fixture_path.read_text(), cs)
    assert offline.value.error_type is ErrorType.CONFLICT

    with pytest.raises(PscError) as live:
        plan_xapi_ops(cs)
    assert live.value.error_type is ErrorType.CONFLICT
