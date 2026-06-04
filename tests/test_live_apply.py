"""Live `--apply`: push a ChangeSet over the XML API (issue #1).

Never touches a real device — the pan-os-python boundary is monkeypatched with
a fake whose `xapi` records every set/edit/delete call and whose `commit` flags
if it was ever invoked. These pin the safety-critical contract:

- a blocked plan writes *nothing* (the `blockers` gate holds on the live path too);
- a clean apply mutates the candidate config but **never commits** — the operator
  owns the commit.
"""

from __future__ import annotations

import functools

import panos.panorama
import pytest

from psc.core.apply_live import plan_xapi_ops
from psc.core.changeset import (
    ChangeSet,
    ObjectDelete,
    ObjectKind,
    ObjectRename,
    ReferenceEdit,
)
from psc.core.source import LiveSource
from psc.output.errors import ErrorType, PscError


class _RecordingXapi:
    """Stand-in for pan-os-python's xapi: records mutating calls."""

    def __init__(self) -> None:
        self.ssl_context = None
        self.calls: list[tuple[str, str]] = []  # (op, xpath)

    def set(self, xpath: str, element: str, **kwargs: object) -> None:
        self.calls.append(("set", xpath))

    def edit(self, xpath: str, element: str, **kwargs: object) -> None:
        self.calls.append(("edit", xpath))

    def delete(self, xpath: str, **kwargs: object) -> None:
        self.calls.append(("delete", xpath))

    def rename(self, xpath: str, newname: str, **kwargs: object) -> None:
        self.calls.append(("rename", xpath))


class _FakePano:
    """Stand-in for the Panorama device object on the write path."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.xapi = _RecordingXapi()
        self.committed = False

    def commit(self, *args: object, **kwargs: object) -> None:
        self.committed = True


@pytest.fixture
def fake_pano(monkeypatch: pytest.MonkeyPatch) -> _FakePano:
    pano = _FakePano()
    monkeypatch.setattr(panos.panorama, "Panorama", functools.partial(lambda *a, **k: pano))
    return pano


def _live() -> LiveSource:
    return LiveSource("pano.example", "LUFRPT1KEYABC123", verify=False)


def _clean_changeset() -> ChangeSet:
    return ChangeSet(
        title="merge dup",
        reference_edits=[
            ReferenceEdit(
                referrer_kind="address-group",
                referrer_name="grp",
                referrer_location="shared",
                field="static",
                before=["dup-host"],
                after=["host"],
            )
        ],
        deletes=[ObjectDelete(kind=ObjectKind.ADDRESS, name="dup-host", location="shared")],
    )


def test_live_apply_refuses_blocked_plan(fake_pano: _FakePano) -> None:
    cs = _clean_changeset()
    cs.blockers.append("cross-scope reference cannot be repointed")
    with pytest.raises(PscError) as ei:
        _live().apply(cs, out_path=None)
    assert ei.value.error_type is ErrorType.CONFLICT
    # The gate must hold *before* any device write.
    assert fake_pano.xapi.calls == []
    assert fake_pano.committed is False


def test_live_apply_writes_candidate_but_never_commits(fake_pano: _FakePano) -> None:
    cs = _clean_changeset()
    result = _live().apply(cs, out_path=None)

    assert result.applied is True
    assert result.ops == cs.op_count
    assert result.out_path is None
    # The plan reached the device...
    assert fake_pano.xapi.calls, "expected at least one xapi mutation"
    # ...but the operator owns the commit.
    assert fake_pano.committed is False


def test_live_apply_repoints_before_delete(fake_pano: _FakePano) -> None:
    """Reference rewrites must hit the wire before the delete of the merged-away
    object — never strand a still-referenced delete on a production device.
    """
    _live().apply(_clean_changeset(), out_path=None)
    ops = [op for op, _ in fake_pano.xapi.calls]
    assert "delete" in ops
    first_delete = ops.index("delete")
    # Every non-delete (the reference repoint) precedes the first delete.
    assert all(op != "delete" for op in ops[:first_delete])
    assert first_delete > 0


def test_live_apply_rename_uses_rename_action(fake_pano: _FakePano) -> None:
    """A rename must use the XML-API `rename` action against the object's
    entry xpath — not a delete+recreate that would strand references.
    """
    cs = ChangeSet(
        title="rename",
        renames=[
            ObjectRename(
                kind=ObjectKind.ADDRESS, location="dg1", old_name="h-old", new_name="h-new"
            )
        ],
    )
    _live().apply(cs, out_path=None)
    assert ("rename", op_xpath := fake_pano.xapi.calls[0][1]) in fake_pano.xapi.calls
    assert "address/entry[@name='h-old']" in op_xpath
    assert "device-group/entry[@name='dg1']" in op_xpath
    assert fake_pano.committed is False


def test_live_apply_rejects_quote_in_name_before_writing(fake_pano: _FakePano) -> None:
    """A name carrying a single quote can't be addressed by an xpath predicate;
    reject it up front rather than send a malformed xpath to a live device.
    """
    cs = ChangeSet(
        title="bad name",
        deletes=[ObjectDelete(kind=ObjectKind.ADDRESS, name="ho'st", location="shared")],
    )
    with pytest.raises(PscError) as ei:
        _live().apply(cs, out_path=None)
    assert ei.value.error_type is ErrorType.INPUT
    assert fake_pano.xapi.calls == []


# -- the pure planner: xpath construction, device-free -------------------


def test_plan_shared_delete_xpath() -> None:
    cs = ChangeSet(
        title="d",
        deletes=[ObjectDelete(kind=ObjectKind.SERVICE, name="svc", location="shared")],
    )
    (op,) = plan_xapi_ops(cs)
    assert op.action == "delete"
    assert op.xpath == "/config/shared/service/entry[@name='svc']"


def test_plan_device_group_reference_edit_xpath_and_members() -> None:
    cs = ChangeSet(
        title="r",
        reference_edits=[
            ReferenceEdit(
                referrer_kind="address-group",
                referrer_name="g",
                referrer_location="dg1",
                field="static",
                after=["a", "b"],
            )
        ],
    )
    (op,) = plan_xapi_ops(cs)
    assert op.action == "edit"
    assert op.xpath == (
        "/config/devices/entry[@name='localhost.localdomain']"
        "/device-group/entry[@name='dg1']/address-group/entry[@name='g']/static"
    )
    assert op.element == "<static><member>a</member><member>b</member></static>"


def test_plan_empty_reference_edit_clears_field_with_delete() -> None:
    cs = ChangeSet(
        title="clear",
        reference_edits=[
            ReferenceEdit(
                referrer_kind="service-group",
                referrer_name="g",
                referrer_location="shared",
                field="members",
                before=["x"],
                after=[],
            )
        ],
    )
    (op,) = plan_xapi_ops(cs)
    assert op.action == "delete"
    assert op.xpath.endswith("/service-group/entry[@name='g']/members")


def test_plan_skips_nat_translation_field() -> None:
    """A NAT translation field has no flat member list — the planner emits no
    op (the renderer already flagged it `# REVIEW`), it must not invent an xpath.
    """
    cs = ChangeSet(
        title="nat",
        reference_edits=[
            ReferenceEdit(
                referrer_kind="nat-rule",
                referrer_name="n",
                referrer_location="dg1",
                field="source-translation",
                rulebase="pre",
                after=["x"],
            )
        ],
    )
    assert plan_xapi_ops(cs) == []
