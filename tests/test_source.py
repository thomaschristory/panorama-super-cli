"""Offline `apply` artifact-format behaviour (#37: write `set` script instead of XML)."""

from __future__ import annotations

from pathlib import Path

import pytest

from psc.core.changeset import ChangeSet, ObjectDelete, ObjectKind
from psc.core.source import ConfigFormat, OfflineSource
from psc.output.errors import PscError

FIXTURE = Path(__file__).parent / "fixtures" / "panorama-config.xml"


def _delete_cs() -> ChangeSet:
    return ChangeSet(
        title="t",
        deletes=[ObjectDelete(kind=ObjectKind.ADDRESS, name="web-primary", location="shared")],
    )


def test_apply_set_format_writes_set_script_not_xml(tmp_path: Path) -> None:
    out = tmp_path / "plan.set"
    res = OfflineSource(FIXTURE).apply(_delete_cs(), out_path=out, out_format=ConfigFormat.SET)
    text = out.read_text(encoding="utf-8")
    assert "delete shared address web-primary" in text
    assert "<entry" not in text  # the artifact is a set script, never XML
    assert res.set_script  # the rendered lines ride back on the result
    assert res.out_path == str(out)


def test_apply_xml_format_is_the_default(tmp_path: Path) -> None:
    out = tmp_path / "fixed.xml"
    res = OfflineSource(FIXTURE).apply(_delete_cs(), out_path=out)
    text = out.read_text(encoding="utf-8")
    assert "<" in text  # still rewritten XML when no format is asked for
    assert not res.set_script


def test_apply_set_format_still_refuses_overwriting_source() -> None:
    with pytest.raises(PscError):
        OfflineSource(FIXTURE).apply(_delete_cs(), out_path=FIXTURE, out_format=ConfigFormat.SET)


def test_apply_set_format_still_requires_out_path() -> None:
    with pytest.raises(PscError):
        OfflineSource(FIXTURE).apply(_delete_cs(), out_path=None, out_format=ConfigFormat.SET)


def test_apply_set_format_refuses_blocked_plan_and_writes_nothing(tmp_path: Path) -> None:
    # The blocker gate is enforced at the applier level for every format, not
    # only in the CLI — a blocked plan must never reach a written artifact.
    out = tmp_path / "plan.set"
    blocked = ChangeSet(
        title="t",
        blockers=["cross-scope reference can't be repointed"],
        deletes=[ObjectDelete(kind=ObjectKind.ADDRESS, name="web-primary", location="shared")],
    )
    with pytest.raises(PscError):
        OfflineSource(FIXTURE).apply(blocked, out_path=out, out_format=ConfigFormat.SET)
    assert not out.exists()
