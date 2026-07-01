from __future__ import annotations

from psc.core.changeset import ChangeSet
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
