from __future__ import annotations

from psc.core.changeset import ChangeSet
from psc.tui.widgets.review import can_apply, review_lines


def test_review_lines_show_warnings_and_set_script():
    cs = ChangeSet(title="merge web", warnings=["web-srv-02 in 2 rules"])
    lines = review_lines(cs)
    text = "\n".join(lines)
    assert "merge web" in text
    assert "! web-srv-02 in 2 rules" in text


def test_review_lines_flag_blockers():
    cs = ChangeSet(title="bad", blockers=["cross-scope reference"])
    text = "\n".join(review_lines(cs))
    assert "BLOCKED" in text
    assert "cross-scope reference" in text


def test_can_apply_false_when_blocked():
    assert can_apply(ChangeSet(title="bad", blockers=["x"])) is False
    assert can_apply(ChangeSet(title="ok")) is True
