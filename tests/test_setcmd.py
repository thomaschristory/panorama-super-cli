from __future__ import annotations

from psc.core.changeset import ChangeSet, ObjectDelete, ObjectKind, ReferenceEdit
from psc.core.models import SHARED, Address, AddressType
from psc.core.setcmd import (
    address_lines,
    reference_edit_lines,
    render_changeset,
    scope_prefix,
)


def test_new_rulebase_reference_edit_set_lines() -> None:
    edit = ReferenceEdit(
        referrer_kind="sdwan-rule",
        referrer_name="sdwan-1",
        referrer_location="shared",
        field="destination",
        rulebase="pre",
        before=["a2-dup", "a1"],
        after=["a2", "a1"],
    )
    lines = reference_edit_lines(edit)
    assert "delete shared pre-rulebase sdwan rules sdwan-1 destination" in lines
    assert "set shared pre-rulebase sdwan rules sdwan-1 destination [ a2 a1 ]" in lines


def test_pbf_nexthop_edit_renders_review_comment() -> None:
    edit = ReferenceEdit(
        referrer_kind="pbf-rule",
        referrer_name="pbf-1",
        referrer_location="shared",
        field="nexthop",
        rulebase="pre",
        before=["nh-host"],
        after=["nh-dup"],
    )
    (line,) = reference_edit_lines(edit)
    assert line.startswith("# REVIEW")
    assert "pbf-1" in line and "nexthop" in line


def test_scope_prefix() -> None:
    assert scope_prefix("shared") == "shared"
    assert scope_prefix("DG-EDGE") == "device-group DG-EDGE"


def test_address_set_lines() -> None:
    a = Address(
        name="h1",
        location=SHARED,
        type=AddressType.IP_NETMASK,
        value="1.1.1.1/32",
        description="x",
        tags=["t"],
    )
    lines = address_lines(a)
    assert "set shared address h1 ip-netmask 1.1.1.1/32" in lines
    assert any("description" in ln for ln in lines)
    assert any("tag [ t ]" in ln for ln in lines)


def test_member_edit_is_delete_then_set() -> None:
    cs = ChangeSet(
        title="t",
        reference_edits=[
            ReferenceEdit(
                referrer_kind="address-group",
                referrer_name="g",
                referrer_location="shared",
                field="static",
                before=["a", "b"],
                after=["a"],
            )
        ],
        deletes=[ObjectDelete(kind=ObjectKind.ADDRESS, name="b", location="shared")],
    )
    out = render_changeset(cs)
    assert "delete shared address-group g static" in out
    assert "set shared address-group g static [ a ]" in out
    assert "delete shared address b" in out
    # delete-field precedes set, and object delete is last
    assert out.index("delete shared address-group g static") < out.index(
        "set shared address-group g static [ a ]"
    )


def test_blocked_changeset_renders_no_ops() -> None:
    cs = ChangeSet(
        title="t",
        blockers=["nope"],
        deletes=[ObjectDelete(kind=ObjectKind.ADDRESS, name="b", location="shared")],
    )
    out = render_changeset(cs)
    assert any("BLOCKED" in ln for ln in out)
    assert not any(ln.startswith("delete shared address b") for ln in out)
