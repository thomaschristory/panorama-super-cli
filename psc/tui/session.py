"""WorkbenchSession — state container + staging engine (framework-free).

Everything the TUI displays reads from `working_snapshot`. Mutations compound
onto `working_xml` via the existing pure `apply_changeset`, so each new plan is
built against reality (prior staged edits already applied).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from psc.core.apply_xml import apply_changeset
from psc.core.changeset import ChangeSet
from psc.core.models import Snapshot
from psc.core.parse import parse_config
from psc.core.resolve import find_ip
from psc.core.setcmd import render_changeset
from psc.core.source import LiveSource, OfflineSource
from psc.output.errors import ErrorType, PscError
from psc.tui.state import ApplyOutcome, OutputMode, SelectionItem, StagedChange


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
        self.staging: list[StagedChange] = []

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
        except ValueError:  # unparseable (non-IP/range/fqdn) queries yield no IP hits
            fr = None
        if fr is not None:
            for m in fr.matches:
                item = SelectionItem(kind="address", name=m.name, location=m.location)
                found[item.key] = item
            for gm in fr.groups:
                item = SelectionItem(kind="address-group", name=gm.name, location=gm.location)
                found[item.key] = item

        return list(found.values())

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
        # Stage onto temporaries and promote atomically: if apply_changeset or
        # parse_config raises, self.working_xml/working_snapshot stay consistent
        # with each other (no half-applied state).
        new_xml = apply_changeset(self.working_xml, cs)
        new_snapshot = parse_config(new_xml)
        self.working_xml = new_xml
        self.working_snapshot = new_snapshot
        self.staging.append(StagedChange(label=label, changeset=cs))
        self._reconcile_selection()

    def _reconcile_selection(self) -> None:
        """Drop selection items that no longer exist in the working snapshot."""
        live = set(_iter_objects(self.working_snapshot))
        self.selection = [i for i in self.selection if i.key in live]

    def combined_set_script(self) -> str:
        """All staged changes as one ordered PAN-OS set/delete script."""
        lines: list[str] = []
        for staged in self.staging:
            lines.extend(render_changeset(staged.changeset))
        return "\n".join(lines)

    def apply_batch(self, *, out_path: str | None) -> ApplyOutcome:
        """Apply the staged batch per `output_mode`. Read-only until here.

        Does not clear `self.staging` on success: the operator decides when to
        clear (they may want to re-export the same batch in another mode).
        """
        # Number of staged *changes* applied (not raw XML op count — this is a
        # TUI-facing tally, distinct from ApplyResult.ops).
        ops = len(self.staging)
        if ops == 0:
            return ApplyOutcome(
                mode=self.output_mode, ops=0, out_path=None, detail="nothing staged"
            )

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
            if (
                isinstance(self.source, OfflineSource)
                and dest.resolve() == self.source.path.resolve()
            ):
                raise PscError("output path must differ from the source config", ErrorType.CONFIG)
            try:
                dest.write_text(self.working_xml, encoding="utf-8")
            except OSError as exc:
                raise PscError(f"cannot write to {dest}: {exc}", ErrorType.INPUT) from exc
            return ApplyOutcome(
                mode=self.output_mode, ops=ops, out_path=str(dest), detail=f"wrote {dest}"
            )

        # LIVE_APPLY: replay each staged changeset in order to the candidate.
        # Each push is independent: a failure mid-loop leaves the preceding
        # changesets in the candidate (uncommitted). Nothing is ever committed,
        # so the operator can inspect or revert (`load config` / `revert
        # config`). A transactional wrapper is a future concern.
        for staged in self.staging:
            self.source.apply(staged.changeset, out_path=None)
        return ApplyOutcome(
            mode=self.output_mode, ops=ops, out_path=None, detail="pushed to candidate"
        )
