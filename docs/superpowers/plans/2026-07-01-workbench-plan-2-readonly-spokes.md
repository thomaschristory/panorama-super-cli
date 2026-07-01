# Workbench Plan 2 — Read-only Spokes (usage/refs + audit) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two read-only spokes to the workbench — **Usage** (where-used for the selected objects) and **Audit** (address overlap/containment involving the selection) — each entered from the hub with the current selection, displaying results, staging nothing.

**Architecture:** Builds on Plan 1's `WorkbenchSession` + hub. Each spoke is a `Screen` in `psc/tui/screens/` plus a pure, unit-testable helper that runs the relevant `psc.core` engine over the selection. Read-only: no `session.stage`, no mutation. Hub gains two bindings (`u`, `a`) that push the screens.

**Tech Stack:** Python 3.12, Textual 8.2.8, pytest + Pilot. Engines: `psc.core.refs.ReferenceGraph`, `psc.core.audit.find_overlapping_addresses`.

**Reference:** spec `docs/superpowers/specs/2026-07-01-workbench-tui-design.md`; Plan 1 `docs/superpowers/plans/2026-07-01-workbench-plan-1-foundation.md`. The session API (from Plan 1) exposes `session.working_snapshot`, `session.selection` (list of `SelectionItem(kind, name, location)`), and `session.selected_of_kinds(kinds)`.

---

## File Structure

- `psc/tui/screens/usage.py` — `UsageScreen` + pure `selection_where_used(session)`.
- `psc/tui/screens/audit.py` — `AuditScreen` + pure `selection_overlaps(session)`.
- `psc/tui/app.py` — two new BINDINGS (`u`→usage, `a`→audit) + `action_usage`/`action_audit`.
- `tests/tui/test_usage.py`, `tests/tui/test_audit.py` — pure-helper unit tests.
- `tests/tui/conftest.py` — extend the fixture so refs + overlaps have data (a group referencing an address, and a contained address).

Kind strings: `"address"`, `"address-group"`, `"service"`, `"service-group"`, `"tag"` (match `changeset.ObjectKind`).

Location conversion (used by both spokes): a location *name* string → `Location`:
```python
from psc.core.models import Location
def _loc(name: str) -> Location:
    return Location.shared() if name == "shared" else Location.dg(name)
```

---

### Task 1: Extend the test fixture for refs + overlaps

**Files:**
- Modify: `tests/tui/conftest.py`

The Plan 1 fixture has three `/32` addresses + one service in `shared`. Add (a) an address-group `web-pool` referencing `web-srv-01` (so where-used has an edge), and (b) a broader network `net-10-0-5` = `10.0.5.0/24` that CONTAINS `web-srv-01` (`10.0.5.10/32`) so the audit finds a containment pair.

- [ ] **Step 1: Update the fixture XML**

In `tests/tui/conftest.py`, replace the `<address>...</address>` block and add an `<address-group>` block inside `<shared>` so it reads:

```python
WORKBENCH_XML = """<?xml version="1.0"?>
<config>
  <shared>
    <address>
      <entry name="web-srv-01"><ip-netmask>10.0.5.10/32</ip-netmask></entry>
      <entry name="web-srv-02"><ip-netmask>10.0.5.10/32</ip-netmask></entry>
      <entry name="db-gw"><ip-netmask>10.0.9.1/32</ip-netmask></entry>
      <entry name="net-10-0-5"><ip-netmask>10.0.5.0/24</ip-netmask></entry>
    </address>
    <address-group>
      <entry name="web-pool"><static><member>web-srv-01</member></static></entry>
    </address-group>
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
```

- [ ] **Step 2: Verify the fixture parses with the new objects**

Run: `uv run python -c "from psc.core.parse import parse_config; from tests.tui.conftest import WORKBENCH_XML; s=parse_config(WORKBENCH_XML); print(len(s.addresses), len(s.address_groups), len(s.services))"`
Expected: prints `4 1 1`.

- [ ] **Step 3: Confirm existing tests still pass**

Run: `just test tests/tui -q`
Expected: PASS. (Plan 1 tests search "srv" → still exactly web-srv-01/02; "10.0.5.10" still finds the two /32s. `net-10-0-5` has a different value so it doesn't disturb those assertions. If any Plan 1 test now fails because the new objects change a count, STOP and report — do not weaken Plan 1 tests without cause.)

- [ ] **Step 4: Commit**

```bash
git add tests/tui/conftest.py
git commit -m "test(tui): extend workbench fixture with a group + containing network"
```

---

### Task 2: Usage spoke (where-used for the selection)

**Files:**
- Create: `psc/tui/screens/usage.py`
- Modify: `psc/tui/app.py`
- Test: `tests/tui/test_usage.py`

The pure helper runs `ReferenceGraph.build(snapshot)` and collects `graph.where_used(kind, name, loc)` for every selected object, returning display rows. The screen shows them in a DataTable; escape returns to the hub. No staging.

- [ ] **Step 1: Write the failing test**

Create `tests/tui/test_usage.py`:

```python
from __future__ import annotations

from psc.core.source import OfflineSource
from psc.tui.screens.usage import UsageRow, selection_where_used
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode, SelectionItem


def _session(workbench_xml: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(workbench_xml), output_mode=OutputMode.SET)


def test_where_used_finds_group_referrer(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    sess.toggle(SelectionItem(kind="address", name="web-srv-01", location="shared"))
    rows = selection_where_used(sess)
    # web-srv-01 is referenced by the address-group web-pool
    assert any(
        isinstance(r, UsageRow)
        and r.object_name == "web-srv-01"
        and r.referrer_kind == "address-group"
        and r.referrer_name == "web-pool"
        for r in rows
    )


def test_where_used_empty_for_unreferenced(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    sess.toggle(SelectionItem(kind="address", name="db-gw", location="shared"))
    rows = selection_where_used(sess)
    assert rows == []


def test_where_used_only_considers_selection(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    # nothing selected -> nothing to report
    assert selection_where_used(sess) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test tests/tui/test_usage.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'psc.tui.screens.usage'`.

- [ ] **Step 3: Write the implementation**

Create `psc/tui/screens/usage.py`:

```python
"""Usage spoke: where-used for the selected objects (read-only, never stages)."""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from psc.core.models import Location
from psc.core.refs import ReferenceGraph
from psc.tui.session import WorkbenchSession


def _loc(name: str) -> Location:
    return Location.shared() if name == "shared" else Location.dg(name)


@dataclass(frozen=True)
class UsageRow:
    """One where-used edge for a selected object."""

    object_kind: str
    object_name: str
    object_location: str
    referrer_kind: str
    referrer_name: str
    referrer_location: str
    field: str


def selection_where_used(session: WorkbenchSession) -> list[UsageRow]:
    """Every reference to each selected object, across the working snapshot."""
    if not session.selection:
        return []
    graph = ReferenceGraph.build(session.working_snapshot)
    rows: list[UsageRow] = []
    for item in session.selection:
        for ref in graph.where_used(item.kind, item.name, _loc(item.location)):
            rows.append(
                UsageRow(
                    object_kind=item.kind,
                    object_name=item.name,
                    object_location=item.location,
                    referrer_kind=ref.referrer_kind,
                    referrer_name=ref.referrer_name,
                    referrer_location=ref.referrer_location.name,
                    field=ref.field,
                )
            )
    return rows


class UsageScreen(Screen[None]):
    BINDINGS = [("escape", "app.pop_screen", "back")]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self._rows = selection_where_used(session)

    def compose(self) -> ComposeResult:
        if not self._rows:
            yield Static("No references to the selected objects.", id="usage-empty")
        else:
            table: DataTable[str] = DataTable(id="usage-table")
            yield table
        yield Footer()

    def on_mount(self) -> None:
        if self._rows:
            table = self.query_one("#usage-table", DataTable)
            table.add_columns("object", "referrer kind", "referrer", "location", "field")
            for r in self._rows:
                table.add_row(
                    r.object_name, r.referrer_kind, r.referrer_name, r.referrer_location, r.field
                )
```

Notes for the implementer:
- `graph.where_used(kind, name, location)` returns `list[Reference]`; each `Reference` has `.referrer_kind`, `.referrer_name`, `.referrer_location` (a `Location`), `.field`. Confirm by reading `psc/core/refs.py` if a test fails.
- If mypy --strict wants a precise `DataTable` type parameter, `DataTable[str]` (as annotated) is correct for string cells. Adapt if the installed Textual differs.

- [ ] **Step 4: Wire the hub binding.** In `psc/tui/app.py`, add to `BINDINGS` (after the `("d", "dedup", "dedup")` line):

```python
        ("u", "usage", "usage"),
```

and add the action method (near `action_dedup`):

```python
    def action_usage(self) -> None:
        from psc.tui.screens.usage import UsageScreen  # noqa: PLC0415 — avoid import cycle

        self.push_screen(UsageScreen(self.session))
```

- [ ] **Step 5: Run tests**

Run: `just test tests/tui/test_usage.py -q`
Expected: PASS.
Run: `just test tests/tui -q`
Expected: PASS (no regressions).

- [ ] **Step 6: Lint**

Run: `just lint`
Expected: PASS. Confirm `grep -nE "import psc\.cli" psc/tui/screens/usage.py` is empty.

- [ ] **Step 7: Commit**

```bash
git add psc/tui/screens/usage.py psc/tui/app.py tests/tui/test_usage.py
git commit -m "feat(tui): usage spoke — where-used for the selection"
```

---

### Task 3: Audit spoke (overlaps involving the selection)

**Files:**
- Create: `psc/tui/screens/audit.py`
- Modify: `psc/tui/app.py`
- Test: `tests/tui/test_audit.py`

The pure helper runs `find_overlapping_addresses(snapshot)` over the whole snapshot, then keeps only pairs where at least one side is a **selected** address (so the report is scoped to what the user picked). The screen displays them; escape returns. No staging.

- [ ] **Step 1: Write the failing test**

Create `tests/tui/test_audit.py`:

```python
from __future__ import annotations

from psc.core.source import OfflineSource
from psc.tui.screens.audit import selection_overlaps
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode, SelectionItem


def _session(workbench_xml: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(workbench_xml), output_mode=OutputMode.SET)


def test_overlaps_finds_containment_involving_selection(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    # net-10-0-5 (10.0.5.0/24) CONTAINS web-srv-01 (10.0.5.10/32)
    sess.toggle(SelectionItem(kind="address", name="web-srv-01", location="shared"))
    pairs = selection_overlaps(sess)
    names = {(p.left_name, p.right_name) for p in pairs}
    assert ("net-10-0-5", "web-srv-01") in names


def test_overlaps_empty_when_selection_not_involved(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    # db-gw (10.0.9.1/32) overlaps nothing
    sess.toggle(SelectionItem(kind="address", name="db-gw", location="shared"))
    assert selection_overlaps(sess) == []


def test_overlaps_empty_without_selection(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    assert selection_overlaps(sess) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test tests/tui/test_audit.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'psc.tui.screens.audit'`.

- [ ] **Step 3: Write the implementation**

Create `psc/tui/screens/audit.py`:

```python
"""Audit spoke: address overlaps/containment involving the selection (read-only)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from psc.core.audit import OverlapPair, find_overlapping_addresses
from psc.tui.session import WorkbenchSession


def selection_overlaps(session: WorkbenchSession) -> list[OverlapPair]:
    """Overlap/containment pairs where at least one side is a selected address."""
    selected = {
        (i.location, i.name) for i in session.selected_of_kinds({"address"})
    }
    if not selected:
        return []
    pairs = find_overlapping_addresses(session.working_snapshot)
    return [
        p
        for p in pairs
        if (p.left_location, p.left_name) in selected
        or (p.right_location, p.right_name) in selected
    ]


class AuditScreen(Screen[None]):
    BINDINGS = [("escape", "app.pop_screen", "back")]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self._pairs = selection_overlaps(session)

    def compose(self) -> ComposeResult:
        if not self._pairs:
            yield Static("No overlaps involving the selected addresses.", id="audit-empty")
        else:
            table: DataTable[str] = DataTable(id="audit-table")
            yield table
        yield Footer()

    def on_mount(self) -> None:
        if self._pairs:
            table = self.query_one("#audit-table", DataTable)
            table.add_columns("relationship", "left", "left value", "right", "right value")
            for p in self._pairs:
                table.add_row(
                    p.relationship.value, p.left_name, p.left_value, p.right_name, p.right_value
                )
```

Notes: `find_overlapping_addresses(snapshot, scope=None) -> list[OverlapPair]`; `OverlapPair` has `.left_name`, `.left_location`, `.left_value`, `.right_name`, `.right_location`, `.right_value`, `.relationship` (an `OverlapKind` enum with `.value`). Confirm against `psc/core/audit.py`.

- [ ] **Step 4: Wire the hub binding.** In `psc/tui/app.py`, add to `BINDINGS` (after the `("u", "usage", "usage")` line):

```python
        ("a", "audit", "audit"),
```

and add:

```python
    def action_audit(self) -> None:
        from psc.tui.screens.audit import AuditScreen  # noqa: PLC0415 — avoid import cycle

        self.push_screen(AuditScreen(self.session))
```

- [ ] **Step 5: Run tests**

Run: `just test tests/tui/test_audit.py -q`
Expected: PASS.
Run: `just test tests/tui -q`
Expected: PASS.

- [ ] **Step 6: Lint**

Run: `just lint`
Expected: PASS. Confirm no `psc.cli` import in `psc/tui/screens/audit.py`.

- [ ] **Step 7: Commit**

```bash
git add psc/tui/screens/audit.py psc/tui/app.py tests/tui/test_audit.py
git commit -m "feat(tui): audit spoke — address overlaps involving the selection"
```

---

### Task 4: Hub integration Pilot test

**Files:**
- Test: append to `tests/tui/test_app_pilot.py`

Prove both read-only spokes open from the hub and render without error.

- [ ] **Step 1: Write the test**

Append to `tests/tui/test_app_pilot.py`:

```python
@pytest.mark.asyncio
async def test_usage_spoke_opens_from_hub(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "web-srv-01"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("u")
        await pilot.pause()
        from psc.tui.screens.usage import UsageScreen

        assert isinstance(app.screen, UsageScreen)
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_audit_spoke_opens_from_hub(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "web-srv-01"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        from psc.tui.screens.audit import AuditScreen

        assert isinstance(app.screen, AuditScreen)
        await pilot.press("escape")
        await pilot.pause()
```

- [ ] **Step 2: Run + lint**

Run: `just test tests/tui -q`
Expected: PASS (all).
Run: `just lint`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/tui/test_app_pilot.py
git commit -m "test(tui): hub opens usage and audit spokes"
```

---

## Self-Review

**Spec coverage (Plan 2 slice):**
- Usage/refs spoke — where-used for the selection, read-only → Task 2. ✓
- Audit spoke — overlaps/containment involving the selection, read-only → Task 3. ✓
- Both entered from the hub with the selection; neither stages → Tasks 2–4. ✓
- Boundary: both screens import only `psc.core` → lint checks in Tasks 2/3. ✓

**Placeholder scan:** No TBD/TODO; every step carries literal code.

**Type consistency:** `selection_where_used → list[UsageRow]`, `selection_overlaps → list[OverlapPair]`; `_loc(name)` used in usage; kind strings match `ObjectKind`. Both actions follow the Plan 1 spoke pattern (`action_*` lazy-imports the screen and `push_screen`s it).

**Known execution risk:** exact Textual `DataTable[str]` typing and `Reference`/`OverlapPair` field names — verify against the installed Textual 8.2.8 and `psc/core/refs.py`/`audit.py`; the pure-helper unit tests are the authoritative acceptance criteria.
