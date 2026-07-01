# Workbench Plan 3 — Mutating Spokes (move / decommission / rename / rule-edit) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the four mutating spokes to the workbench — **Move-to-shared**, **Decommission**, **Rename**, and **Rule member-edit** — each entered from the hub with the current selection, each planning a `ChangeSet` via its `psc.core` engine and staging it through the existing `session.stage` (blocker-gated, compounding).

**Architecture:** Builds on Plan 1 (session + staging + ReviewPanel) and Plan 2 (read-only spokes). Every mutating spoke follows the Plan 1 dedup pattern: a pure `plan_*` helper (unit-tested) builds a `ChangeSet`; a `Screen` shows it in a `ReviewPanel` and stages it on `ctrl+y` via `session.stage`, then pops + refreshes the hub. `session.stage` already hard-gates blocked changesets and reconciles the selection — the spokes add no new safety logic.

**Scope note (important):** the core has **no rule-creation engine** — only `rule_edit.plan_rule_member_edit` (add/remove a member of an *existing* rule's field). So the "rule" spoke is scoped to **member editing existing rules**, not creating new ones. Rule creation is deferred (needs a new core engine) and is out of scope here.

**Tech Stack:** Python 3.12, Textual 8.2.8, pytest + Pilot. Engines: `psc.core.relocate.plan_move`, `psc.core.decommission.plan_decommission`, `psc.core.naming.plan_rename`, `psc.core.rule_edit.plan_rule_member_edit`, `psc.core.refs.ReferenceGraph`, `psc.core.changeset.ObjectKind`, `psc.core.models.{Location, Rulebase}`.

**Reference:** spec `docs/superpowers/specs/2026-07-01-workbench-tui-design.md`; Plans 1 & 2. Session API: `session.working_snapshot`, `session.selection` (`SelectionItem(kind, name, location)`), `session.selected_of_kinds(kinds)`, `session.stage(label, cs)`. `ObjectKind(item.kind)` maps a selection kind string to the enum (values are identical). Location conversion: `Location.shared() if name == "shared" else Location.dg(name)`.

---

## File Structure

- `psc/tui/screens/move.py` — `MoveScreen` + pure `movable_items(session)` + `plan_move_item(session, item, dest_name)`.
- `psc/tui/screens/decommission.py` — `DecommissionScreen` + pure `plan_selection_decommission(session)`.
- `psc/tui/screens/rename.py` — `RenameScreen` + pure `plan_rename_item(session, item, new_name)`.
- `psc/tui/screens/rule.py` — `RuleScreen` + pure `plan_rule_add_member(session, rule_name, rulebase, field, member_name)`.
- `psc/tui/app.py` — four new BINDINGS (`m`→move, `x`→decommission, `r`→rename, `e`→rule-edit) + `action_*`.
- `tests/tui/test_move.py`, `test_decommission.py`, `test_rename.py`, `test_rule.py` — pure-helper unit tests.
- `tests/tui/conftest.py` — extend with a device-group + one security rule (for the rule spoke).

The hub already binds `d`/`u`/`a`. New keys: `m` (move), `x` (decommission — destructive), `r` (rename), `e` (rule edit). (`r` is free; the hub has no `r` binding yet.)

A shared cross-object staging helper appears in three spokes (move, and any looped spoke) — stage each planned `ChangeSet` in order, stopping on the first blocked one. Keep it as a small local method per screen (they differ in what they plan); do not over-abstract for v1.

---

### Task 1: Move-to-shared spoke

**Files:**
- Create: `psc/tui/screens/move.py`
- Modify: `psc/tui/app.py`
- Test: `tests/tui/test_move.py`

Moves each selected object that is not already in `shared` toward `shared`. Zero extra input. Each object is planned against the **current** working snapshot right before it is staged (so compounding stays correct).

- [ ] **Step 1: Write the failing test**

Create `tests/tui/test_move.py`:

```python
from __future__ import annotations

from psc.core.source import OfflineSource
from psc.tui.screens.move import movable_items, plan_move_item
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode, SelectionItem


def _session(workbench_xml: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(workbench_xml), output_mode=OutputMode.SET)


def test_movable_items_excludes_shared(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    # every fixture object is in shared already
    sess.toggle(SelectionItem(kind="address", name="web-srv-01", location="shared"))
    assert movable_items(sess) == []


def test_movable_items_includes_device_group_object(workbench_xml_dg: str) -> None:
    sess = _session(workbench_xml_dg)
    item = SelectionItem(kind="address", name="dg-only", location="dg1")
    sess.toggle(item)
    assert movable_items(sess) == [item]


def test_plan_move_item_to_shared_is_not_blocked(workbench_xml_dg: str) -> None:
    sess = _session(workbench_xml_dg)
    item = SelectionItem(kind="address", name="dg-only", location="dg1")
    cs = plan_move_item(sess, item, "shared")
    assert not cs.is_blocked
    assert not cs.is_empty
```

This needs a fixture with a device-group object. Add `workbench_xml_dg` to `tests/tui/conftest.py`:

```python
WORKBENCH_XML_DG = """<?xml version="1.0"?>
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
      </device-group>
    </entry>
  </devices>
</config>
"""


@pytest.fixture
def workbench_xml_dg(tmp_path):
    """A config with an object inside device-group dg1 (for move/rename)."""
    p = tmp_path / "config_dg.xml"
    p.write_text(WORKBENCH_XML_DG, encoding="utf-8")
    return str(p)
```

Before implementing, verify the DG fixture parses: run
`uv run python -c "from psc.core.parse import parse_config; from tests.tui.conftest import WORKBENCH_XML_DG; s=parse_config(WORKBENCH_XML_DG); print([(a.name,a.location.name) for a in s.addresses])"`
Expected: shows `('anchor','shared')` and `('dg-only','dg1')`. If `dg-only`'s location is not `dg1`, read `psc/core/parse.py` for the exact device-group XML nesting it expects and fix the fixture minimally.

- [ ] **Step 2: Run the test to verify it fails**

Run: `just test tests/tui/test_move.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'psc.tui.screens.move'`.

- [ ] **Step 3: Write the implementation**

Create `psc/tui/screens/move.py`:

```python
"""Move spoke: promote selected objects toward shared (stages a ChangeSet each)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Static

from psc.core.changeset import ChangeSet, ObjectKind
from psc.core.refs import ReferenceGraph
from psc.core.relocate import plan_move
from psc.tui.session import WorkbenchSession
from psc.tui.state import SelectionItem
from psc.tui.widgets.review import can_apply

if TYPE_CHECKING:
    from psc.tui.app import WorkbenchApp

_MOVABLE_KINDS = {"address", "address-group", "service", "service-group", "tag"}


def movable_items(session: WorkbenchSession) -> list[SelectionItem]:
    """Selected objects of a movable kind that are not already in shared."""
    return [
        i
        for i in session.selected_of_kinds(_MOVABLE_KINDS)
        if i.location != "shared"
    ]


def plan_move_item(session: WorkbenchSession, item: SelectionItem, dest_name: str) -> ChangeSet:
    """Plan promoting one selected object toward `dest_name` (e.g. 'shared')."""
    graph = ReferenceGraph.build(session.working_snapshot)
    return plan_move(
        session.working_snapshot,
        graph,
        kind=ObjectKind(item.kind),
        name=item.name,
        source_name=item.location,
        dest_name=dest_name,
    )


class MoveScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("ctrl+y", "stage", "move to shared"),
        ("escape", "app.pop_screen", "cancel"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        self._items = movable_items(session)

    def compose(self) -> ComposeResult:
        if not self._items:
            yield Static("No selected objects outside shared to move.", id="move-empty")
        else:
            names = ", ".join(f"{i.name}@{i.location}" for i in self._items)
            yield Static(f"Move to shared: {names}\n[ctrl+y] confirm  [esc] cancel", id="move-plan")
        yield Footer()

    def action_stage(self) -> None:
        if not self._items:
            self.app.bell()
            return
        # Re-plan each item against the CURRENT snapshot right before staging so
        # compounding stays correct; stop on the first blocked plan.
        for item in list(self._items):
            cs = plan_move_item(self.session, item, "shared")
            if not can_apply(cs):
                self.app.bell()
                break
            self.session.stage(f"move {item.name} -> shared", cs)
        self.app.pop_screen()
        cast("WorkbenchApp", self.app)._refresh_selection_view()
```

- [ ] **Step 4: Wire the hub.** In `psc/tui/app.py` BINDINGS add `("m", "move", "move")`, and:

```python
    def action_move(self) -> None:
        from psc.tui.screens.move import MoveScreen  # noqa: PLC0415 — avoid import cycle

        self.push_screen(MoveScreen(self.session))
```

- [ ] **Step 5: Run tests + lint**

Run: `just test tests/tui/test_move.py -q` → PASS.
Run: `just test tests/tui -q` → PASS (no regressions).
Run: `just lint` → PASS. Confirm no `psc.cli` import in `psc/tui/screens/move.py`.

- [ ] **Step 6: Commit**

```bash
git add psc/tui/screens/move.py psc/tui/app.py tests/tui/test_move.py tests/tui/conftest.py
git commit -m "feat(tui): move spoke — promote selected objects to shared"
```

---

### Task 2: Decommission spoke (destructive)

**Files:**
- Create: `psc/tui/screens/decommission.py`
- Modify: `psc/tui/app.py`
- Test: `tests/tui/test_decommission.py`

Reference-safe cascading teardown of the selected **address** objects. Single `ChangeSet` from `plan_decommission`. Because it is destructive, the screen requires an explicit confirm and shows a prominent warning; staging still routes through the blocker-gated `session.stage`.

- [ ] **Step 1: Write the failing test**

Create `tests/tui/test_decommission.py`:

```python
from __future__ import annotations

from psc.core.source import OfflineSource
from psc.tui.screens.decommission import plan_selection_decommission
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode, SelectionItem


def _session(workbench_xml: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(workbench_xml), output_mode=OutputMode.SET)


def test_decommission_plans_delete_for_selected_address(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    sess.toggle(SelectionItem(kind="address", name="db-gw", location="shared"))
    cs = plan_selection_decommission(sess)
    assert cs is not None
    assert not cs.is_empty
    assert any(d.name == "db-gw" for d in cs.deletes)


def test_decommission_none_without_address_selection(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    assert plan_selection_decommission(sess) is None
    sess.toggle(SelectionItem(kind="service", name="tcp-8443", location="shared"))
    assert plan_selection_decommission(sess) is None


def test_decommission_reconciles_after_stage(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    item = SelectionItem(kind="address", name="db-gw", location="shared")
    sess.toggle(item)
    cs = plan_selection_decommission(sess)
    assert cs is not None
    sess.stage("decommission db-gw", cs)
    assert item not in sess.selection  # deleted object drops out
```

(`ChangeSet.deletes` is the list of `ObjectDelete` with `.name`. Verify against `psc/core/changeset.py`.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `just test tests/tui/test_decommission.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

Create `psc/tui/screens/decommission.py`:

```python
"""Decommission spoke: reference-safe teardown of selected address objects."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer

from psc.core.changeset import ChangeSet
from psc.core.decommission import plan_decommission
from psc.core.models import Address
from psc.core.refs import ReferenceGraph
from psc.tui.session import WorkbenchSession
from psc.tui.widgets.review import ReviewPanel, can_apply

if TYPE_CHECKING:
    from psc.tui.app import WorkbenchApp


def plan_selection_decommission(session: WorkbenchSession) -> ChangeSet | None:
    """Plan teardown of the selected address objects, or None if none selected."""
    selected = session.selected_of_kinds({"address"})
    if not selected:
        return None
    index = {(a.location.name, a.name): a for a in session.working_snapshot.addresses}
    targets: list[Address] = []
    for item in selected:
        obj = index.get((item.location, item.name))
        if obj is not None:
            targets.append(obj)
    if not targets:
        return None
    graph = ReferenceGraph.build(session.working_snapshot)
    return plan_decommission(session.working_snapshot, graph, targets)


class DecommissionScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("ctrl+y", "stage", "confirm teardown"),
        ("escape", "app.pop_screen", "cancel"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        self._plan = plan_selection_decommission(session)

    def compose(self) -> ComposeResult:
        yield ReviewPanel(id="review")
        yield Footer()

    def on_mount(self) -> None:
        panel = self.query_one("#review", ReviewPanel)
        if self._plan is None:
            panel.update("Select one or more address objects to decommission.")
        else:
            panel.show(self._plan)

    def action_stage(self) -> None:
        if self._plan is None or not can_apply(self._plan):
            self.app.bell()
            return
        self.session.stage("decommission address objects", self._plan)
        self.app.pop_screen()
        cast("WorkbenchApp", self.app)._refresh_selection_view()
```

- [ ] **Step 4: Wire the hub.** In `psc/tui/app.py` BINDINGS add `("x", "decommission", "decommission")`, and:

```python
    def action_decommission(self) -> None:
        from psc.tui.screens.decommission import DecommissionScreen  # noqa: PLC0415 — avoid cycle

        self.push_screen(DecommissionScreen(self.session))
```

- [ ] **Step 5: Run tests + lint**

`just test tests/tui/test_decommission.py -q` → PASS; `just test tests/tui -q` → PASS; `just lint` → PASS (no `psc.cli` import in the new file).

- [ ] **Step 6: Commit**

```bash
git add psc/tui/screens/decommission.py psc/tui/app.py tests/tui/test_decommission.py
git commit -m "feat(tui): decommission spoke — reference-safe teardown of selected addresses"
```

---

### Task 3: Rename spoke

**Files:**
- Create: `psc/tui/screens/rename.py`
- Modify: `psc/tui/app.py`
- Test: `tests/tui/test_rename.py`

Renames the **first** selected object to a new name typed into an `Input` (reference-aware, via `naming.plan_rename`). Operates on one object per activation.

- [ ] **Step 1: Write the failing test**

Create `tests/tui/test_rename.py`:

```python
from __future__ import annotations

from psc.core.source import OfflineSource
from psc.tui.screens.rename import first_renameable, plan_rename_item
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode, SelectionItem


def _session(workbench_xml: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(workbench_xml), output_mode=OutputMode.SET)


def test_first_renameable_returns_first_selected(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    a = SelectionItem(kind="address", name="db-gw", location="shared")
    sess.toggle(a)
    assert first_renameable(sess) == a


def test_first_renameable_none_when_empty(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    assert first_renameable(sess) is None


def test_plan_rename_item_builds_reference_aware_rename(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    # web-srv-01 is referenced by web-pool; renaming must repoint that group.
    item = SelectionItem(kind="address", name="web-srv-01", location="shared")
    cs = plan_rename_item(sess, item, "web-server-01")
    assert not cs.is_blocked
    assert not cs.is_empty
    assert any(r.new_name == "web-server-01" for r in cs.renames)
```

(`ChangeSet.renames` is a list of `ObjectRename` with `.new_name`/`.old_name`. Verify against `psc/core/changeset.py`.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `just test tests/tui/test_rename.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

Create `psc/tui/screens/rename.py`:

```python
"""Rename spoke: reference-aware rename of the first selected object."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Input, Static

from psc.core.changeset import ChangeSet, ObjectKind
from psc.core.naming import plan_rename
from psc.core.refs import ReferenceGraph
from psc.tui.session import WorkbenchSession
from psc.tui.state import SelectionItem
from psc.tui.widgets.review import can_apply

if TYPE_CHECKING:
    from psc.tui.app import WorkbenchApp


def first_renameable(session: WorkbenchSession) -> SelectionItem | None:
    """The first selected object (any kind), or None if the selection is empty."""
    return session.selection[0] if session.selection else None


def plan_rename_item(session: WorkbenchSession, item: SelectionItem, new_name: str) -> ChangeSet:
    """Plan a reference-aware rename of `item` to `new_name`."""
    graph = ReferenceGraph.build(session.working_snapshot)
    return plan_rename(
        session.working_snapshot,
        graph,
        kind=ObjectKind(item.kind),
        location_name=item.location,
        old_name=item.name,
        new_name=new_name,
    )


class RenameScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("escape", "app.pop_screen", "cancel"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        self._item = first_renameable(session)

    def compose(self) -> ComposeResult:
        if self._item is None:
            yield Static("Select an object to rename.", id="rename-empty")
        else:
            yield Static(f"Rename {self._item.kind} '{self._item.name}'@{self._item.location} to:")
            yield Input(placeholder="new name (Enter to stage)", id="rename-input")
        yield Footer()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "rename-input" or self._item is None:
            return
        new_name = event.value.strip()
        if not new_name:
            self.app.bell()
            return
        cs = plan_rename_item(self.session, self._item, new_name)
        if not can_apply(cs):
            self.app.bell()
            return
        self.session.stage(f"rename {self._item.name} -> {new_name}", cs)
        self.app.pop_screen()
        cast("WorkbenchApp", self.app)._refresh_selection_view()
```

- [ ] **Step 4: Wire the hub.** In `psc/tui/app.py` BINDINGS add `("r", "rename", "rename")`, and:

```python
    def action_rename(self) -> None:
        from psc.tui.screens.rename import RenameScreen  # noqa: PLC0415 — avoid import cycle

        self.push_screen(RenameScreen(self.session))
```

- [ ] **Step 5: Run tests + lint**

`just test tests/tui/test_rename.py -q` → PASS; `just test tests/tui -q` → PASS; `just lint` → PASS (no `psc.cli` import in the new file).

- [ ] **Step 6: Commit**

```bash
git add psc/tui/screens/rename.py psc/tui/app.py tests/tui/test_rename.py
git commit -m "feat(tui): rename spoke — reference-aware rename of a selected object"
```

---

### Task 4: Rule member-edit spoke (+ fixture with a rule)

**Files:**
- Modify: `tests/tui/conftest.py` (add a security rule)
- Create: `psc/tui/screens/rule.py`
- Modify: `psc/tui/app.py`
- Test: `tests/tui/test_rule.py`

Adds each selected object's name as a member of an existing rule's field (`source`/`destination`/`service`/`application`), via `rule_edit.plan_rule_member_edit`. The rule name and field are typed; the rulebase defaults to `pre`.

- [ ] **Step 1: Add a rule to the fixture**

The existing `WORKBENCH_XML` has no rules. Add a shared pre-rulebase security rule so the spoke has a target. In `tests/tui/conftest.py`, inside `<shared>` of `WORKBENCH_XML` (after the `<service>` block), add:

```xml
    <pre-rulebase>
      <security>
        <rules>
          <entry name="allow-web">
            <from><member>any</member></from>
            <to><member>any</member></to>
            <source><member>web-srv-01</member></source>
            <destination><member>any</member></destination>
            <service><member>any</member></service>
            <application><member>any</member></application>
            <action>allow</action>
          </entry>
        </rules>
      </security>
    </pre-rulebase>
```

Verify it parses to a security rule: run
`uv run python -c "from psc.core.parse import parse_config; from tests.tui.conftest import WORKBENCH_XML; s=parse_config(WORKBENCH_XML); print([(r.name, r.rulebase) for r in s.security_rules])"`
Expected: shows `allow-web` with rulebase `pre` (or the parser's representation). If it parses to zero security rules, read `psc/core/parse.py`'s `_parse_security_rules` and the scope/rulebase walk to learn the exact expected XML nesting (shared vs device-group, `pre-rulebase`/`post-rulebase` tag names) and fix the fixture minimally so exactly one security rule named `allow-web` appears. Confirm the Plan 1/2 tests still pass afterward (`just test tests/tui -q`).

- [ ] **Step 2: Write the failing test**

Create `tests/tui/test_rule.py`:

```python
from __future__ import annotations

from psc.core.models import Rulebase
from psc.core.source import OfflineSource
from psc.tui.screens.rule import plan_rule_add_member
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode


def _session(workbench_xml: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(workbench_xml), output_mode=OutputMode.SET)


def test_add_member_to_rule_source(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    cs = plan_rule_add_member(sess, "allow-web", Rulebase.PRE, "source", "db-gw")
    # adding db-gw to allow-web's source is a real, non-blocked edit
    assert not cs.is_blocked
    assert not cs.is_empty


def test_add_present_member_is_empty(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    # web-srv-01 is already in allow-web's source -> idempotent no-op
    cs = plan_rule_add_member(sess, "allow-web", Rulebase.PRE, "source", "web-srv-01")
    assert cs.is_empty
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `just test tests/tui/test_rule.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Write the implementation**

Create `psc/tui/screens/rule.py`:

```python
"""Rule spoke: add selected objects as members of an existing rule field."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Input, Static

from psc.core.changeset import ChangeSet
from psc.core.models import Rulebase
from psc.core.rule_edit import plan_rule_member_edit
from psc.tui.session import WorkbenchSession
from psc.tui.widgets.review import can_apply

if TYPE_CHECKING:
    from psc.tui.app import WorkbenchApp

_DEFAULT_FIELD = "source"


def plan_rule_add_member(
    session: WorkbenchSession,
    rule_name: str,
    rulebase: Rulebase,
    field: str,
    member_name: str,
) -> ChangeSet:
    """Plan adding `member_name` to `rule_name`'s `field` (idempotent)."""
    return plan_rule_member_edit(
        session.working_snapshot,
        rule_name,
        None,
        rulebase,
        field,
        add=member_name,
        remove=None,
    )


class RuleScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("escape", "app.pop_screen", "cancel"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        self._members = [i.name for i in session.selection]

    def compose(self) -> ComposeResult:
        if not self._members:
            yield Static("Select objects to add to a rule first.", id="rule-empty")
        else:
            names = ", ".join(self._members)
            yield Static(f"Add [{names}] to a pre-rulebase security rule's field.")
            yield Input(placeholder="rule name", id="rule-name")
            yield Input(value=_DEFAULT_FIELD, placeholder="field (source/destination/service)", id="rule-field")
            yield Static("Fill both, then press Enter on the field box to stage.", id="rule-hint")
        yield Footer()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "rule-field" or not self._members:
            return
        rule_name = self.query_one("#rule-name", Input).value.strip()
        field = event.value.strip() or _DEFAULT_FIELD
        if not rule_name:
            self.app.bell()
            return
        staged = 0
        for member in list(self._members):
            cs = plan_rule_add_member(self.session, rule_name, Rulebase.PRE, field, member)
            if not can_apply(cs):
                self.app.bell()
                break
            if not cs.is_empty:
                self.session.stage(f"add {member} to {rule_name}.{field}", cs)
                staged += 1
        self.app.pop_screen()
        cast("WorkbenchApp", self.app)._refresh_selection_view()
```

Notes:
- `plan_rule_member_edit(snapshot, rule_name, location, rulebase, field, *, add, remove)` raises `PscError` (NOT_FOUND) if the rule doesn't exist — the pure-helper tests use the fixture's `allow-web`, so they won't hit that; the screen's try path is fine because a missing rule surfaces via the exception → the app-level handling is out of scope for this spoke (a bad rule name just bells is acceptable; if the raise escapes in the Pilot test in Task 5, wrap the loop body in a try/except PscError that bells — add it only if a test needs it).
- The screen adds ALL selected members (looping); each stage recompounds.

- [ ] **Step 5: Wire the hub.** In `psc/tui/app.py` BINDINGS add `("e", "rule_edit", "rule")`, and:

```python
    def action_rule_edit(self) -> None:
        from psc.tui.screens.rule import RuleScreen  # noqa: PLC0415 — avoid import cycle

        self.push_screen(RuleScreen(self.session))
```

- [ ] **Step 6: Run tests + lint**

`just test tests/tui/test_rule.py -q` → PASS; `just test tests/tui -q` → PASS; `just lint` → PASS (no `psc.cli` import in the new file).

- [ ] **Step 7: Commit**

```bash
git add psc/tui/screens/rule.py psc/tui/app.py tests/tui/test_rule.py tests/tui/conftest.py
git commit -m "feat(tui): rule spoke — add selected objects to an existing rule field"
```

---

### Task 5: Hub integration Pilot tests for the mutating spokes

**Files:**
- Test: append to `tests/tui/test_app_pilot.py`

Prove the four bindings open their screens, and that the input-free ones (move, decommission) stage from the hub.

- [ ] **Step 1: Write the tests**

Append to `tests/tui/test_app_pilot.py`:

```python
@pytest.mark.asyncio
async def test_decommission_spoke_stages_from_hub(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "db-gw"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("x")           # open decommission
        await pilot.pause()
        await pilot.press("ctrl+y")      # confirm teardown
        await pilot.pause()
        assert len(app.session.staging) == 1
        assert app.session.selection == []   # db-gw deleted -> reconciled out


@pytest.mark.asyncio
async def test_rename_spoke_opens_from_hub(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "db-gw"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("r")           # open rename
        await pilot.pause()
        from psc.tui.screens.rename import RenameScreen

        assert isinstance(app.screen, RenameScreen)


@pytest.mark.asyncio
async def test_move_and_rule_bindings_open(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "db-gw"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("m")           # move screen (no movable items -> empty msg, still opens)
        await pilot.pause()
        from psc.tui.screens.move import MoveScreen

        assert isinstance(app.screen, MoveScreen)
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("e")           # rule screen
        await pilot.pause()
        from psc.tui.screens.rule import RuleScreen

        assert isinstance(app.screen, RuleScreen)
```

- [ ] **Step 2: Run the FULL suite + lint**

Run: `just test tests/tui -q` → PASS (all).
Run: `just test -q` → PASS (whole project, no regressions).
Run: `just lint` → PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/tui/test_app_pilot.py
git commit -m "test(tui): hub opens/stages the four mutating spokes"
```

---

## Self-Review

**Spec coverage (Plan 3 slice — completes the 7-spoke v1):**
- Move-to-shared spoke (relocate engine) → Task 1. ✓
- Decommission spoke (decommission engine, destructive, explicit confirm) → Task 2. ✓
- Rename spoke (naming engine, reference-aware, Input) → Task 3. ✓
- Rule member-edit spoke (rule_edit engine, existing rules only) → Task 4. ✓
- All stage through the blocker-gated `session.stage`; none add new safety logic. ✓
- All import only `psc.core` (+ `psc.tui`); lint checks per task guard the boundary. ✓

**Placeholder scan:** No TBD/TODO; every step carries literal code. The rule-spoke exception handling is explicitly conditional ("add only if a test needs it") — the pure-helper tests don't trigger it.

**Type consistency:** `ObjectKind(item.kind)` maps selection kinds; `plan_move_item`/`plan_rename_item`/`plan_rule_add_member`/`plan_selection_decommission` all return `ChangeSet` (or `ChangeSet | None`) and are staged via `session.stage(label, cs)`; every screen uses `can_apply` before staging and `cast("WorkbenchApp", self.app)._refresh_selection_view()` after (the Plan 1 idiom). Hub keys `m`/`x`/`r`/`e` don't collide with existing `d`/`u`/`a`/`space`/`ctrl+a`/`q`.

**Known execution risks:**
- Fixture XML for the device-group object (Task 1) and the security rule (Task 4) must match `psc/core/parse.py`'s expected nesting — each task has an explicit parse-verification step with a fallback to read the parser.
- `ChangeSet` field names (`deletes[].name`, `renames[].new_name`) — verify against `psc/core/changeset.py`; the pure-helper unit tests are the acceptance criteria.
- Rule spoke: `plan_rule_member_edit` raises on a missing rule; the Pilot test only opens the screen (doesn't submit a bad rule), so the raise isn't exercised there.
