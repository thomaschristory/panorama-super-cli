# Workbench Plan 1 — Foundation & Walking Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a runnable `psc workbench` TUI that lets you search objects, multi-select them into a persistent buffer, route the selection into the **dedup** spoke, stage the resulting merge, and apply the staged batch — end-to-end.

**Architecture:** New `psc/tui/` package that imports only `psc.core` + `psc.output`. A pure, framework-free `WorkbenchSession` owns all state and the staging engine (search → select → stage-with-compounding → apply-batch), reusing the existing `apply_changeset` / `render_changeset` machinery. A thin Textual layer (`WorkbenchApp`, `HubScreen`, `DedupScreen`, `ReviewPanel`) drives that session. Reference: `docs/superpowers/specs/2026-07-01-workbench-tui-design.md`.

**Tech Stack:** Python 3.12, Pydantic v2, Textual (new dep), Typer (entry point), pytest + Textual `Pilot` for tests.

---

## File Structure

- `psc/tui/__init__.py` — package marker.
- `psc/tui/state.py` — pure data: `OutputMode` enum, `SelectionItem`, `StagedChange`, `ApplyOutcome`.
- `psc/tui/session.py` — `WorkbenchSession`: state container + staging engine. **No Textual import.** The safety-critical, fully-unit-tested core.
- `psc/tui/widgets/__init__.py` — package marker.
- `psc/tui/widgets/review.py` — `review_lines(cs)` pure helper + `ReviewPanel` widget.
- `psc/tui/screens/__init__.py` — package marker.
- `psc/tui/screens/dedup.py` — `DedupScreen` (first spoke).
- `psc/tui/app.py` — `WorkbenchApp` + `HubScreen`.
- `psc/tui/workbench.tcss` — orange theme.
- `psc/cli/workbench_cmds.py` — the `workbench` Typer command; wired into `psc/cli/app.py`.
- `tests/tui/__init__.py`, `tests/tui/conftest.py` — fixtures (tiny config XML with duplicates).
- `tests/tui/test_session.py` — session/staging engine unit tests.
- `tests/tui/test_review.py` — review-panel helper tests.
- `tests/tui/test_app_pilot.py` — Textual Pilot flow tests.

Kind strings used throughout (match `changeset.ObjectKind` values): `"address"`, `"address-group"`, `"service"`, `"service-group"`, `"tag"`.

---

### Task 1: Dependency & package skeleton

**Files:**
- Modify: `pyproject.toml` (dependencies list)
- Create: `psc/tui/__init__.py`
- Create: `tests/tui/__init__.py`

- [ ] **Step 1: Add Textual as a mandatory dependency**

In `pyproject.toml`, add `"textual>=0.85"` to the `dependencies` array (after `"packaging>=23.0",`):

```toml
    "packaging>=23.0",
    "textual>=0.85",
```

- [ ] **Step 2: Create the package markers**

Create `psc/tui/__init__.py`:

```python
"""Workbench — the interactive Textual TUI frontend for psc.

Imports only psc.core and psc.output; never psc.cli. See
docs/superpowers/specs/2026-07-01-workbench-tui-design.md.
"""
```

Create `tests/tui/__init__.py` (empty file).

- [ ] **Step 3: Sync and verify Textual imports**

Run: `just sync && python -c "import textual, psc.tui; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml psc/tui/__init__.py tests/tui/__init__.py
git commit -m "feat(tui): add textual dep and psc.tui package skeleton"
```

---

### Task 2: State dataclasses

**Files:**
- Create: `psc/tui/state.py`
- Test: `tests/tui/test_session.py`

- [ ] **Step 1: Write the failing test**

Create `tests/tui/test_session.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test tests/tui/test_session.py -q` (or `uv run pytest tests/tui/test_session.py -q`)
Expected: FAIL with `ModuleNotFoundError: No module named 'psc.tui.state'`.

- [ ] **Step 3: Write minimal implementation**

Create `psc/tui/state.py`:

```python
"""Pure state types for the workbench. No Textual, no engine logic."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from psc.core.changeset import ChangeSet


class OutputMode(str, Enum):
    """How a staged batch is finally applied."""

    SET = "set"              # render combined PAN-OS set script, push nothing
    OFFLINE_APPLY = "offline-apply"  # write the compounded config to a file
    LIVE_APPLY = "live-apply"        # replay changesets to the live candidate


@dataclass(frozen=True)
class SelectionItem:
    """One object reference held in the selection buffer (heterogeneous)."""

    kind: str          # "address" | "address-group" | "service" | "service-group" | "tag"
    name: str
    location: str      # location *name* ("shared" or a device-group name)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.kind, self.name, self.location)


@dataclass
class StagedChange:
    """One entry in the git-like staged changelist."""

    label: str
    changeset: ChangeSet


@dataclass
class ApplyOutcome:
    """Result of applying the staged batch."""

    mode: OutputMode
    ops: int
    out_path: str | None
    detail: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `just test tests/tui/test_session.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add psc/tui/state.py tests/tui/test_session.py
git commit -m "feat(tui): workbench state dataclasses"
```

---

### Task 3: Test fixture — a config with duplicates

**Files:**
- Create: `tests/tui/conftest.py`

- [ ] **Step 1: Create the fixture**

Create `tests/tui/conftest.py`. This is a minimal Panorama export XML with two byte-identical address objects (`web-srv-01`, `web-srv-02` both `10.0.5.10/32`) in `shared`, plus a service, so dedup and search have real data.

```python
from __future__ import annotations

import pytest

WORKBENCH_XML = """<?xml version="1.0"?>
<config>
  <shared>
    <address>
      <entry name="web-srv-01"><ip-netmask>10.0.5.10/32</ip-netmask></entry>
      <entry name="web-srv-02"><ip-netmask>10.0.5.10/32</ip-netmask></entry>
      <entry name="db-gw"><ip-netmask>10.0.9.1/32</ip-netmask></entry>
    </address>
    <service>
      <entry name="tcp-8443"><protocol><tcp><port>8443</port></tcp></protocol></entry>
    </service>
  </shared>
  <devices>
    <entry name="localhost.localdomain">
      <device-group/>
    </entry>
  </devices>
</config>
"""


@pytest.fixture
def workbench_xml(tmp_path):
    """Write the fixture config to a temp file, return its path (str)."""
    p = tmp_path / "config.xml"
    p.write_text(WORKBENCH_XML, encoding="utf-8")
    return str(p)
```

- [ ] **Step 2: Verify the fixture parses**

Run: `just psc --config <path-to-a-copy> find ip 10.0.5.10` is not needed here; instead verify parsing headlessly:

Run: `uv run python -c "from psc.core.parse import parse_config; from tests.tui.conftest import WORKBENCH_XML; s=parse_config(WORKBENCH_XML); print(len(s.addresses), len(s.services))"`
Expected: prints `3 1`.

- [ ] **Step 3: Commit**

```bash
git add tests/tui/conftest.py
git commit -m "test(tui): workbench config fixture with duplicate addresses"
```

---

### Task 4: `WorkbenchSession` — construction & search

**Files:**
- Create: `psc/tui/session.py`
- Test: `tests/tui/test_session.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/tui/test_session.py`:

```python
from psc.core.source import OfflineSource
from psc.tui.session import WorkbenchSession


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
    # "10.0.5.10" matches by IP; each object should appear once, not twice.
    hits = sess.search("10.0.5.10")
    keys = [h.key for h in hits]
    assert len(keys) == len(set(keys))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test tests/tui/test_session.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'psc.tui.session'`.

- [ ] **Step 3: Write minimal implementation**

Create `psc/tui/session.py`:

```python
"""WorkbenchSession — state container + staging engine (framework-free).

Everything the TUI displays reads from `working_snapshot`. Mutations compound
onto `working_xml` via the existing pure `apply_changeset`, so each new plan is
built against reality (prior staged edits already applied).
"""

from __future__ import annotations

from collections.abc import Iterator

from psc.core.models import Snapshot
from psc.core.parse import parse_config
from psc.core.resolve import find_ip
from psc.core.source import LiveSource, OfflineSource
from psc.tui.state import OutputMode, SelectionItem


def _iter_objects(snapshot: Snapshot) -> Iterator[tuple[str, str, str]]:
    """Yield (kind, name, location_name) for every selectable object."""
    for a in snapshot.addresses:
        yield ("address", a.name, a.location.name)
    for g in snapshot.address_groups:
        yield ("address-group", g.name, g.location.name)
    for s in snapshot.services:
        yield ("service", s.name, s.location.name)
    for sg in snapshot.service_groups:
        yield ("service-group", sg.name, sg.location.name)
    for t in snapshot.tags:
        yield ("tag", t.name, t.location.name)


class WorkbenchSession:
    def __init__(
        self,
        source: OfflineSource | LiveSource,
        *,
        output_mode: OutputMode,
    ) -> None:
        self.source = source
        self.output_mode = output_mode
        self.working_xml: str = source.raw_xml()
        self.working_snapshot: Snapshot = parse_config(self.working_xml)
        self.selection: list[SelectionItem] = []

    def search(self, query: str) -> list[SelectionItem]:
        """Search the working snapshot by name substring and by IP/value."""
        q = query.strip()
        if not q:
            return []
        found: dict[tuple[str, str, str], SelectionItem] = {}

        # Name substring across all kinds.
        ql = q.lower()
        for kind, name, loc in _iter_objects(self.working_snapshot):
            if ql in name.lower():
                item = SelectionItem(kind=kind, name=name, location=loc)
                found[item.key] = item

        # IP / value match (address objects + groups). Guarded: a non-IP query
        # simply yields nothing here.
        try:
            fr = find_ip(self.working_snapshot, q)
        except Exception:  # noqa: BLE001 — non-address queries are expected
            fr = None
        if fr is not None:
            for m in fr.matches:
                item = SelectionItem(kind="address", name=m.name, location=m.location)
                found[item.key] = item
            for gm in fr.groups:
                item = SelectionItem(kind="address-group", name=gm.name, location=gm.location)
                found[item.key] = item

        return list(found.values())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `just test tests/tui/test_session.py -q`
Expected: PASS (all session tests green).

- [ ] **Step 5: Commit**

```bash
git add psc/tui/session.py tests/tui/test_session.py
git commit -m "feat(tui): WorkbenchSession construction and search"
```

---

### Task 5: Selection buffer operations

**Files:**
- Modify: `psc/tui/session.py`
- Test: `tests/tui/test_session.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/tui/test_session.py`:

```python
def test_toggle_adds_then_removes(workbench_xml):
    sess = _session(workbench_xml)
    item = SelectionItem(kind="address", name="web-srv-01", location="shared")
    assert sess.toggle(item) is True          # added
    assert sess.selection == [item]
    assert sess.toggle(item) is False         # removed
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test tests/tui/test_session.py -q`
Expected: FAIL with `AttributeError: 'WorkbenchSession' object has no attribute 'toggle'`.

- [ ] **Step 3: Write minimal implementation**

Add these methods to `WorkbenchSession` in `psc/tui/session.py`:

```python
    def toggle(self, item: SelectionItem) -> bool:
        """Add `item` if absent (by key), remove it if present. Returns True
        when the result is 'now selected'."""
        for existing in self.selection:
            if existing.key == item.key:
                self.selection.remove(existing)
                return False
        self.selection.append(item)
        return True

    def selected_of_kinds(self, kinds: set[str]) -> list[SelectionItem]:
        return [i for i in self.selection if i.kind in kinds]

    def clear_selection(self) -> None:
        self.selection.clear()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `just test tests/tui/test_session.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add psc/tui/session.py tests/tui/test_session.py
git commit -m "feat(tui): selection buffer toggle/filter/clear"
```

---

### Task 6: Staging engine — `stage()` with compounding + selection reconcile

**Files:**
- Modify: `psc/tui/session.py`
- Test: `tests/tui/test_session.py`

This is the safety-critical core. Staging a `ChangeSet` compounds it onto `working_xml`, re-derives `working_snapshot`, and drops selection items that no longer exist.

- [ ] **Step 1: Write the failing test**

Append to `tests/tui/test_session.py`:

```python
from psc.core.dedup import ObjectRef, plan_merge
from psc.core.refs import ReferenceGraph
from psc.output.errors import PscError


def _merge_web_dupes_cs(sess):
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
    # staged
    assert [s.label for s in sess.staging] == ["merge web dupes"]
    # compounded: the dropped object is gone from the working snapshot
    assert not any(a.name == "web-srv-02" for a in sess.working_snapshot.addresses)
    assert any(a.name == "web-srv-01" for a in sess.working_snapshot.addresses)


def test_stage_reconciles_selection_dropping_dead_items(workbench_xml):
    sess = _session(workbench_xml)
    keep = SelectionItem(kind="address", name="web-srv-01", location="shared")
    drop = SelectionItem(kind="address", name="web-srv-02", location="shared")
    sess.toggle(keep)
    sess.toggle(drop)
    sess.stage("merge web dupes", _merge_web_dupes_cs(sess))
    # survivor stays, merged-away dupe drops out
    assert sess.selection == [keep]


def test_stage_refuses_blocked_changeset(workbench_xml):
    sess = _session(workbench_xml)
    cs = ChangeSet(title="bad", blockers=["cannot repoint cross-scope reference"])
    with pytest.raises(PscError):
        sess.stage("bad", cs)
    assert sess.staging == []


def test_second_stage_plans_against_compounded_reality(workbench_xml):
    sess = _session(workbench_xml)
    sess.stage("merge web dupes", _merge_web_dupes_cs(sess))
    # web-srv-02 no longer exists; a search for it returns nothing now.
    assert sess.search("web-srv-02") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test tests/tui/test_session.py -q`
Expected: FAIL with `AttributeError: 'WorkbenchSession' object has no attribute 'stage'`.

- [ ] **Step 3: Write minimal implementation**

Add to the imports in `psc/tui/session.py`:

```python
from psc.core.apply_xml import apply_changeset
from psc.core.changeset import ChangeSet
from psc.output.errors import ErrorType, PscError
from psc.tui.state import StagedChange
```

Add a `self.staging: list[StagedChange] = []` line to `__init__` (after `self.selection = []`), then add:

```python
    def stage(self, label: str, cs: ChangeSet) -> None:
        """Compound `cs` onto the working config and record it. A blocked
        changeset is refused (hard gate), exactly like the CLI."""
        if cs.is_blocked:
            raise PscError(
                "cannot stage a blocked change: " + "; ".join(cs.blockers),
                ErrorType.CONFLICT,
                details={"blockers": cs.blockers, "warnings": cs.warnings},
            )
        if cs.is_empty:
            return
        self.working_xml = apply_changeset(self.working_xml, cs)
        self.working_snapshot = parse_config(self.working_xml)
        self.staging.append(StagedChange(label=label, changeset=cs))
        self._reconcile_selection()

    def _reconcile_selection(self) -> None:
        """Drop selection items that no longer exist in the working snapshot."""
        live = set(_iter_objects(self.working_snapshot))
        self.selection = [i for i in self.selection if i.key in live]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `just test tests/tui/test_session.py -q`
Expected: PASS.

- [ ] **Step 5: Run lint to keep the core clean**

Run: `just lint`
Expected: PASS (no `psc.cli` import in `psc/tui/session.py`; mypy clean).

- [ ] **Step 6: Commit**

```bash
git add psc/tui/session.py tests/tui/test_session.py
git commit -m "feat(tui): staging engine — compounding stage + selection reconcile"
```

---

### Task 7: `apply_batch()` + combined set script

**Files:**
- Modify: `psc/tui/session.py`
- Test: `tests/tui/test_session.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/tui/test_session.py`:

```python
from psc.tui.state import ApplyOutcome, OutputMode


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test tests/tui/test_session.py -q`
Expected: FAIL with `AttributeError: ... 'combined_set_script'`.

- [ ] **Step 3: Write minimal implementation**

Add to imports in `psc/tui/session.py`:

```python
from pathlib import Path

from psc.core.setcmd import render_changeset
from psc.tui.state import ApplyOutcome, OutputMode
```

Add methods to `WorkbenchSession`:

```python
    def combined_set_script(self) -> str:
        """All staged changes as one ordered PAN-OS set/delete script."""
        lines: list[str] = []
        for staged in self.staging:
            lines.extend(render_changeset(staged.changeset))
        return "\n".join(lines)

    def apply_batch(self, *, out_path: str | None) -> ApplyOutcome:
        """Apply the staged batch per `output_mode`. Read-only until here."""
        ops = len(self.staging)
        if ops == 0:
            return ApplyOutcome(mode=self.output_mode, ops=0, out_path=None, detail="nothing staged")

        if self.output_mode is OutputMode.SET:
            script = self.combined_set_script()
            return ApplyOutcome(mode=self.output_mode, ops=ops, out_path=None, detail=script)

        if self.output_mode is OutputMode.OFFLINE_APPLY:
            if out_path is None:
                raise PscError(
                    "offline apply needs an output path (the compounded config "
                    "is never written back over the source export)",
                    ErrorType.CONFIG,
                )
            dest = Path(out_path)
            if isinstance(self.source, OfflineSource) and dest.resolve() == self.source.path.resolve():
                raise PscError("output path must differ from the source config", ErrorType.CONFIG)
            dest.write_text(self.working_xml, encoding="utf-8")
            return ApplyOutcome(
                mode=self.output_mode, ops=ops, out_path=str(dest), detail=f"wrote {dest}"
            )

        # LIVE_APPLY: replay each staged changeset in order to the candidate.
        for staged in self.staging:
            self.source.apply(staged.changeset, out_path=None)
        return ApplyOutcome(
            mode=self.output_mode, ops=ops, out_path=None, detail="pushed to candidate"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `just test tests/tui/test_session.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add psc/tui/session.py tests/tui/test_session.py
git commit -m "feat(tui): apply_batch (set/offline/live) + combined set script"
```

---

### Task 8: `review_lines()` + `ReviewPanel` widget

**Files:**
- Create: `psc/tui/widgets/__init__.py`
- Create: `psc/tui/widgets/review.py`
- Test: `tests/tui/test_review.py`

- [ ] **Step 1: Write the failing test**

Create `tests/tui/test_review.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test tests/tui/test_review.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'psc.tui.widgets.review'`.

- [ ] **Step 3: Write minimal implementation**

Create `psc/tui/widgets/__init__.py` (empty).

Create `psc/tui/widgets/review.py`:

```python
"""Review panel: renders a ChangeSet's plan (set script + warnings + blockers).

`review_lines`/`can_apply` are pure so they unit-test without a running app;
`ReviewPanel` is the thin Textual wrapper the screens mount.
"""

from __future__ import annotations

from textual.widgets import Static

from psc.core.changeset import ChangeSet
from psc.core.setcmd import render_changeset


def can_apply(cs: ChangeSet) -> bool:
    """Blocked changesets can never be staged/applied — the hard gate."""
    return not cs.is_blocked


def review_lines(cs: ChangeSet) -> list[str]:
    lines = [f"[b]{cs.title}[/b]"]
    for w in cs.warnings:
        lines.append(f"  [yellow]! {w}[/yellow]")
    if cs.is_blocked:
        lines.append("[red]BLOCKED — will not apply:[/red]")
        lines.extend(f"  [red]- {b}[/red]" for b in cs.blockers)
        return lines
    for line in render_changeset(cs):
        lines.append(f"  {line}")
    return lines


class ReviewPanel(Static):
    """Displays the plan for a ChangeSet; `can_apply` gates the apply key."""

    def show(self, cs: ChangeSet) -> None:
        self._cs = cs
        self.update("\n".join(review_lines(cs)))

    @property
    def can_apply(self) -> bool:
        return can_apply(getattr(self, "_cs", ChangeSet(title="")))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `just test tests/tui/test_review.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add psc/tui/widgets/__init__.py psc/tui/widgets/review.py tests/tui/test_review.py
git commit -m "feat(tui): ReviewPanel + pure review_lines/can_apply helpers"
```

---

### Task 9: `WorkbenchApp` + `HubScreen` + orange theme

**Files:**
- Create: `psc/tui/workbench.tcss`
- Create: `psc/tui/app.py`
- Test: `tests/tui/test_app_pilot.py`

The hub: a search `Input`, a results `DataTable` (space toggles selection), a selection `DataTable`, and a staging `Static` strip. Key bindings route to spokes and apply.

- [ ] **Step 1: Write the failing test**

Create `tests/tui/test_app_pilot.py`:

```python
from __future__ import annotations

import pytest

from psc.core.source import OfflineSource
from psc.tui.app import WorkbenchApp
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode


def _app(workbench_xml) -> WorkbenchApp:
    sess = WorkbenchSession(source=OfflineSource(workbench_xml), output_mode=OutputMode.SET)
    return WorkbenchApp(sess)


@pytest.mark.asyncio
async def test_search_populates_results(workbench_xml):
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search").value = "srv"
        await pilot.press("enter")
        await pilot.pause()
        table = app.query_one("#results")
        assert table.row_count == 2


@pytest.mark.asyncio
async def test_space_toggles_selection(workbench_xml):
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search").value = "db-gw"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results").focus()
        await pilot.press("space")
        await pilot.pause()
        assert [i.name for i in app.session.selection] == ["db-gw"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test tests/tui/test_app_pilot.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'psc.tui.app'`.

- [ ] **Step 3: Write the theme**

Create `psc/tui/workbench.tcss`:

```css
/* Palo Alto orange accent. */
Screen { background: $surface; }
#search { border: tall #fa582d; }
#results { height: 1fr; border: round #fa582d; }
#selection { height: 1fr; border: round $secondary; }
#staging { height: auto; border: round #fa582d; color: $text; }
DataTable > .datatable--cursor { background: #fa582d; color: black; }
Footer { background: #fa582d; }
```

- [ ] **Step 4: Write minimal implementation**

Create `psc/tui/app.py`:

```python
"""WorkbenchApp + HubScreen — the Textual frontend over WorkbenchSession."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Input, Static

from psc.tui.session import WorkbenchSession
from psc.tui.state import SelectionItem

_TCSS = str(Path(__file__).with_name("workbench.tcss"))


class HubScreen(Static):
    """The home layout. Kept as a container widget for simplicity in v1."""

    def compose(self) -> ComposeResult:
        yield Input(placeholder="search: IP / value / name", id="search")
        with Horizontal():
            yield DataTable(id="results")
            with Vertical():
                yield DataTable(id="selection")
                yield Static("staged (0)", id="staging")


class WorkbenchApp(App):
    CSS_PATH = _TCSS
    TITLE = "psc workbench"
    BINDINGS = [
        ("space", "toggle_row", "select"),
        ("d", "dedup", "dedup"),
        ("ctrl+a", "apply_batch", "apply"),
        ("q", "quit", "quit"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        yield Header()
        yield HubScreen()
        yield Footer()

    def on_mount(self) -> None:
        results = self.query_one("#results", DataTable)
        results.add_columns("kind", "name", "location")
        results.cursor_type = "row"
        sel = self.query_one("#selection", DataTable)
        sel.add_columns("kind", "name", "location")
        # Row index -> SelectionItem, so `space` knows what a row represents.
        self._results: list[SelectionItem] = []

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "search":
            return
        self._results = self.session.search(event.value)
        table = self.query_one("#results", DataTable)
        table.clear()
        for item in self._results:
            table.add_row(item.kind, item.name, item.location)

    def _refresh_selection_view(self) -> None:
        sel = self.query_one("#selection", DataTable)
        sel.clear()
        for i in self.session.selection:
            sel.add_row(i.kind, i.name, i.location)
        self.query_one("#staging", Static).update(f"staged ({len(self.session.staging)})")

    def action_toggle_row(self) -> None:
        table = self.query_one("#results", DataTable)
        if not self._results:
            return
        row = table.cursor_row
        if row is None or row >= len(self._results):
            return
        self.session.toggle(self._results[row])
        self._refresh_selection_view()

    def action_dedup(self) -> None:  # filled in Task 10
        self.bell()

    def action_apply_batch(self) -> None:  # filled in Task 11
        self.bell()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `just test tests/tui/test_app_pilot.py -q`
Expected: PASS. (If `pytest-asyncio` is missing, add `"pytest-asyncio>=0.23"` to the `dev` dependency-group and configure `asyncio_mode = "auto"` in `pyproject.toml` `[tool.pytest.ini_options]`, then re-run.)

- [ ] **Step 6: Commit**

```bash
git add psc/tui/app.py psc/tui/workbench.tcss tests/tui/test_app_pilot.py pyproject.toml
git commit -m "feat(tui): WorkbenchApp + hub screen (search/select) with orange theme"
```

---

### Task 10: `DedupScreen` — the first spoke

**Files:**
- Create: `psc/tui/screens/__init__.py`
- Create: `psc/tui/screens/dedup.py`
- Modify: `psc/tui/app.py` (`action_dedup`)
- Test: `tests/tui/test_app_pilot.py`

The dedup screen takes the current selection, filters to addresses, finds which selected pairs are duplicates, shows the merge plan in a `ReviewPanel`, and on confirm stages the merge changeset.

- [ ] **Step 1: Write the failing test**

Append to `tests/tui/test_app_pilot.py`:

```python
@pytest.mark.asyncio
async def test_dedup_spoke_stages_merge_and_reconciles(workbench_xml):
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search").value = "10.0.5.10"
        await pilot.press("enter")
        await pilot.pause()
        # select both duplicates
        results = app.query_one("#results")
        results.focus()
        await pilot.press("space")           # row 0
        results.move_cursor(row=1)
        await pilot.press("space")           # row 1
        await pilot.pause()
        assert len(app.session.selection) == 2
        # open dedup and stage the proposed merge
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("ctrl+y")          # confirm/stage on the dedup screen
        await pilot.pause()
        assert len(app.session.staging) == 1
        # selection reconciled: the merged-away duplicate is gone
        assert len(app.session.selection) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test tests/tui/test_app_pilot.py::test_dedup_spoke_stages_merge_and_reconciles -q`
Expected: FAIL (dedup screen not implemented; `d` only rings the bell).

- [ ] **Step 3: Write minimal implementation**

Create `psc/tui/screens/__init__.py` (empty).

Create `psc/tui/screens/dedup.py`:

```python
"""Dedup spoke: propose a safe merge for duplicate selected addresses."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Static

from psc.core.changeset import ChangeSet
from psc.core.dedup import ObjectRef, plan_merge
from psc.core.refs import ReferenceGraph
from psc.tui.session import WorkbenchSession
from psc.tui.widgets.review import ReviewPanel, can_apply


def plan_selection_merge(session: WorkbenchSession) -> tuple[str, ChangeSet] | None:
    """First duplicate address pair in the selection → (label, merge plan).

    Returns None when fewer than two selected addresses share a value.
    """
    addrs = session.selected_of_kinds({"address"})
    snap = session.working_snapshot
    index = {(a.location.name, a.name): a for a in snap.addresses}
    by_value: dict[str, list] = {}
    for item in addrs:
        obj = index.get((item.location, item.name))
        if obj is None:
            continue
        by_value.setdefault(obj.value, []).append(item)
    for value, group in by_value.items():
        if len(group) >= 2:
            keep, drop = group[0], group[1]
            graph = ReferenceGraph.build(snap)
            cs = plan_merge(
                snap,
                graph,
                keep=ObjectRef(name=keep.name, location=keep.location),
                drop=ObjectRef(name=drop.name, location=drop.location),
            )
            return (f"merge {drop.name} -> {keep.name}", cs)
    return None


class DedupScreen(Screen):
    BINDINGS = [
        ("ctrl+y", "stage", "stage merge"),
        ("escape", "app.pop_screen", "cancel"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        self._plan = plan_selection_merge(session)

    def compose(self) -> ComposeResult:
        panel = ReviewPanel(id="review")
        yield panel
        if self._plan is None:
            yield Static("No duplicate addresses in the selection.", id="dedup-empty")
        yield Footer()

    def on_mount(self) -> None:
        if self._plan is not None:
            self.query_one("#review", ReviewPanel).show(self._plan[1])

    def action_stage(self) -> None:
        if self._plan is None:
            self.app.bell()
            return
        label, cs = self._plan
        if not can_apply(cs):
            self.app.bell()
            return
        self.session.stage(label, cs)
        self.app.pop_screen()
        # Refresh the hub's selection/staging view after returning.
        self.app._refresh_selection_view()  # type: ignore[attr-defined]
```

Replace `action_dedup` in `psc/tui/app.py` with:

```python
    def action_dedup(self) -> None:
        from psc.tui.screens.dedup import DedupScreen  # noqa: PLC0415 — avoid cycle

        self.push_screen(DedupScreen(self.session))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `just test tests/tui/test_app_pilot.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add psc/tui/screens/__init__.py psc/tui/screens/dedup.py psc/tui/app.py tests/tui/test_app_pilot.py
git commit -m "feat(tui): dedup spoke — plan+stage merge for duplicate selection"
```

---

### Task 11: Apply the staged batch from the hub

**Files:**
- Modify: `psc/tui/app.py` (`action_apply_batch`)
- Test: `tests/tui/test_app_pilot.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/tui/test_app_pilot.py`:

```python
@pytest.mark.asyncio
async def test_apply_batch_offline_writes_file(workbench_xml, tmp_path):
    from psc.tui.state import OutputMode

    sess = WorkbenchSession(source=OfflineSource(workbench_xml), output_mode=OutputMode.OFFLINE_APPLY)
    dest = tmp_path / "candidate.xml"
    sess.apply_out_path = str(dest)  # the hub reads this for offline apply
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        app.query_one("#search").value = "10.0.5.10"
        await pilot.press("enter")
        await pilot.pause()
        results = app.query_one("#results")
        results.focus()
        await pilot.press("space")
        results.move_cursor(row=1)
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("ctrl+y")
        await pilot.pause()
        await pilot.press("ctrl+a")   # apply batch
        await pilot.pause()
    assert dest.exists()
    assert "web-srv-02" not in dest.read_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test tests/tui/test_app_pilot.py::test_apply_batch_offline_writes_file -q`
Expected: FAIL (file not written; `action_apply_batch` only rings the bell).

- [ ] **Step 3: Write minimal implementation**

Add an `apply_out_path` attribute default in `WorkbenchApp.__init__` (after `self.session = session`):

```python
        # Where offline-apply writes the compounded config; set by the launcher.
        self.apply_out_path: str | None = None
```

Since the session carries the out path in the test, read it from either place. Replace `action_apply_batch` in `psc/tui/app.py` with:

```python
    def action_apply_batch(self) -> None:
        out_path = getattr(self.session, "apply_out_path", None) or self.apply_out_path
        try:
            outcome = self.session.apply_batch(out_path=out_path)
        except Exception as exc:  # noqa: BLE001 — surface to the user, don't crash the app
            self.query_one("#staging", Static).update(f"[red]apply failed: {exc}[/red]")
            self.bell()
            return
        self.query_one("#staging", Static).update(
            f"applied {outcome.ops} change(s) — {outcome.detail.splitlines()[0] if outcome.detail else ''}"
        )
```

Note: `apply_out_path` is read off the session when present (the launcher in Task 12 sets it there); the `WorkbenchApp.apply_out_path` attribute is the fallback.

- [ ] **Step 4: Run test to verify it passes**

Run: `just test tests/tui/test_app_pilot.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add psc/tui/app.py tests/tui/test_app_pilot.py
git commit -m "feat(tui): apply staged batch from the hub (ctrl+a)"
```

---

### Task 12: Entry point — `psc workbench` / `psc w`

**Files:**
- Create: `psc/cli/workbench_cmds.py`
- Modify: `psc/cli/app.py`
- Test: `tests/tui/test_entry.py`

- [ ] **Step 1: Write the failing test**

Create `tests/tui/test_entry.py`:

```python
from __future__ import annotations

from psc.cli.workbench_cmds import build_session
from psc.tui.state import OutputMode


def test_build_session_from_offline_config(workbench_xml):
    sess = build_session(config_file=workbench_xml, profile=None, output_mode=OutputMode.SET)
    assert any(a.name == "web-srv-01" for a in sess.working_snapshot.addresses)
    assert sess.output_mode is OutputMode.SET


def test_workbench_command_is_registered():
    from psc.cli.app import app

    names = {cmd.name for cmd in app.registered_commands}
    assert "workbench" in names
    assert "w" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test tests/tui/test_entry.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'psc.cli.workbench_cmds'`.

- [ ] **Step 3: Write minimal implementation**

Create `psc/cli/workbench_cmds.py`:

```python
"""`psc workbench` (alias `psc w`) — launch the interactive TUI."""

from __future__ import annotations

import typer

from psc.cli.runtime import Runtime
from psc.core.source import LiveSource, OfflineSource
from psc.output.errors import ErrorType, PscError
from psc.tui.app import WorkbenchApp
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode


def build_session(
    *,
    config_file: str | None,
    profile: str | None,
    output_mode: OutputMode,
) -> WorkbenchSession:
    """Construct a session from an offline config or a live profile.

    Mirrors Runtime.source(): --config wins; else the named/default profile.
    """
    from psc.config.loader import config_path, load_config  # noqa: PLC0415

    source: OfflineSource | LiveSource
    if config_file:
        source = OfflineSource(config_file)
    else:
        cfg = load_config(config_path())
        prof = cfg.profile(profile)
        if prof is None:
            raise PscError(
                "no source: pass --config <export.xml> or configure a profile",
                ErrorType.CONFIG,
            )
        source = LiveSource(prof.hostname, prof.api_key, port=prof.port, verify=prof.verify_ssl)
    return WorkbenchSession(source=source, output_mode=output_mode)


def workbench(
    ctx: typer.Context,
    apply_out: str | None = typer.Option(
        None, "--apply-out", help="File to write when output mode is offline-apply."
    ),
) -> None:
    """Launch the interactive workbench TUI."""
    rt: Runtime = ctx.obj
    # Default output mode: set (dry-run friendly). Overridable in-app later.
    session = build_session(
        config_file=rt.config_file, profile=rt.profile, output_mode=OutputMode.SET
    )
    session.apply_out_path = apply_out  # type: ignore[attr-defined]
    WorkbenchApp(session).run()
```

In `psc/cli/app.py`, add `workbench_cmds` to the `from psc.cli import (...)` block, then register both the command and its alias after the other `app.command(...)` calls (near line 168):

```python
app.command("workbench", help="Launch the interactive workbench TUI.")(workbench_cmds.workbench)
app.command("w", hidden=True, help="Alias for workbench.")(workbench_cmds.workbench)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `just test tests/tui/test_entry.py -q`
Expected: PASS.

- [ ] **Step 5: Full verification**

Run: `just lint && just test`
Expected: PASS. Confirm no `psc/tui/` module imports `psc.cli` except `workbench_cmds.py` (which is itself in `psc/cli`).

Run manually: `just psc --config tests/tui/... ` is awkward for a TUI; instead launch against a copied fixture: `just psc -c /tmp/config.xml workbench` and confirm the hub renders, search works, `q` quits.

- [ ] **Step 6: Commit**

```bash
git add psc/cli/workbench_cmds.py psc/cli/app.py tests/tui/test_entry.py
git commit -m "feat(tui): psc workbench / psc w entry point"
```

---

## Self-Review

**Spec coverage (Plan 1 slice):**
- New `psc/tui/` frontend importing only core/output → Tasks 1–11 (lint check in Task 6/12 guards the boundary). ✓
- Session state (profile→source, output_mode, working_xml/snapshot, selection, staging) → Tasks 4–7. ✓
- Staging engine with working-XML compounding + selection reconcile → Task 6. ✓
- apply_batch for set/offline/live + blockers gate → Tasks 6–7. ✓
- Heterogeneous selection + multi-kind search → Tasks 4–5. ✓
- Hub screen (search/select/staging strip) → Task 9. ✓
- Dedup spoke entered with selection, filters kinds, plans+stages → Task 10. ✓
- ReviewPanel (set script + warnings + blockers, gate) → Task 8. ✓
- `psc workbench`/`psc w`, orange theme → Tasks 9, 12. ✓
- Deferred to Plan 2/3: usage/refs, audit, rule, naming, move, decommission; in-app output-mode toggle; live-apply Pilot coverage (live path is unit-covered indirectly but not exercised against a device).

**Placeholder scan:** No TBD/TODO; every code step carries literal code. The `action_dedup`/`action_apply_batch` stubs in Task 9 are intentional walking-skeleton placeholders explicitly replaced in Tasks 10–11.

**Type consistency:** `SelectionItem.key` is `(kind, name, location)` everywhere; `stage(label, cs)` / `StagedChange(label, changeset)` names align; `apply_batch(*, out_path)` and `ApplyOutcome(mode, ops, out_path, detail)` consistent across Tasks 7, 11, 12; kind strings match `changeset.ObjectKind` values throughout.

**Known execution risk:** exact Textual widget APIs (`DataTable.move_cursor`, `cursor_row`, `run_test`/`Pilot`) are stable in Textual ≥0.85 but if a call differs, adjust to the installed version — the session-engine tests (the safety-critical core) are framework-independent and authoritative.
