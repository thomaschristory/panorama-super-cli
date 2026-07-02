"""WorkbenchSession — state container + staging engine (framework-free).

Everything the TUI displays reads from `working_snapshot`. Mutations compound
onto `working_xml` via the existing pure `apply_changeset`, so each new plan is
built against reality (prior staged edits already applied).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from psc.core.apply_xml import apply_changeset, partial_config_from_batch
from psc.core.changeset import ChangeSet, ObjectKind
from psc.core.models import AddressGroup, Service, Snapshot
from psc.core.parse import parse_config
from psc.core.portability import export_ndjson
from psc.core.resolve import find_ip
from psc.core.setcmd import render_changeset
from psc.core.source import LiveSource, OfflineSource
from psc.output.errors import ErrorType, PscError
from psc.tui.state import ApplyOutcome, OutputMode, SelectionItem, StagedChange


def _service_value(s: Service) -> str:
    out = f"{s.protocol}/{s.destination_port}" if s.destination_port else s.protocol
    if s.source_port:
        out += f" src:{s.source_port}"
    return out


def _address_group_value(g: AddressGroup) -> str:
    if g.dynamic_filter is not None:
        return f"filter: {g.dynamic_filter}"
    return f"{{{len(g.static_members or [])} members}}"


def render_value(snapshot: Snapshot, item: SelectionItem) -> str:
    """Compact, human-readable value for one selectable object.

    Framework-free (no Textual): the results table calls this per row so two
    same-named-prefix objects with different values are distinguishable. A
    missing object or a `None` field renders as "", never raises. Objects are
    resolved out of `snapshot` by the item's (kind, name, location) identity.
    """
    key = (item.location, item.name)
    if item.kind == "address":
        a = {(o.location.name, o.name): o for o in snapshot.addresses}.get(key)
        return a.value if a else ""
    if item.kind == "service":
        s = {(o.location.name, o.name): o for o in snapshot.services}.get(key)
        return _service_value(s) if s else ""
    if item.kind == "address-group":
        g = {(o.location.name, o.name): o for o in snapshot.address_groups}.get(key)
        return _address_group_value(g) if g else ""
    if item.kind == "service-group":
        sg = {(o.location.name, o.name): o for o in snapshot.service_groups}.get(key)
        return f"{{{len(sg.members)} members}}" if sg else ""
    if item.kind == "tag":
        t = {(o.location.name, o.name): o for o in snapshot.tags}.get(key)
        return (t.color or "") if t else ""
    return ""


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
        self.apply_out_path: str | None = None
        # When True, OFFLINE_APPLY writes a MINIMAL partial config (only the
        # touched subtrees) instead of the whole rewritten document (#92). Full
        # config stays the default — opt-in, no behaviour change otherwise.
        self.offline_partial: bool = False

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

    def remove_at(self, index: int) -> bool:
        """Drop the selection entry at `index` (the selection-panel row order).
        Returns True if an entry was removed."""
        if 0 <= index < len(self.selection):
            del self.selection[index]
            return True
        return False

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

    def drop_staged(self, index: int) -> None:
        """Remove the staged change at `index` and rebuild the working config.

        Staged changes COMPOUND: `working_xml` is `source.raw_xml()` with every
        staged changeset applied in order. Dropping change *k* therefore means
        rebuilding from the source and re-applying every OTHER staged changeset,
        in order — the dropped change's effect vanishes while the rest are
        preserved.

        A later change may depend on an earlier one (e.g. it edits an object the
        earlier change created). If replaying the remaining changesets fails once
        the dependency is gone, this raises a `PscError` and leaves `staging` plus
        `working_xml`/`working_snapshot` completely untouched — the rebuild runs on
        temporaries and is promoted only on full success, so the batch never ends
        up half-rebuilt.
        """
        if not 0 <= index < len(self.staging):
            return  # out-of-range: no-op, batch untouched

        remaining = [s for i, s in enumerate(self.staging) if i != index]
        # Rebuild on temporaries; promote atomically only if every replay succeeds.
        try:
            new_xml = self.source.raw_xml()
            for staged in remaining:
                new_xml = apply_changeset(new_xml, staged.changeset)
            new_snapshot = parse_config(new_xml)
        except PscError as exc:
            raise PscError(
                f"cannot drop staged change {index}: replaying the remaining "
                f"changes failed (a later change likely depended on it) — {exc}; "
                "the batch is left intact",
                ErrorType.CONFLICT,
            ) from exc

        self.staging = remaining
        self.working_xml = new_xml
        self.working_snapshot = new_snapshot
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

        SET is a preview (renders the script, changes nothing, keeps staging) —
        or, when an out path is given, writes the combined set script to that
        file and still keeps staging (writing a script is an export, not a
        commit). OFFLINE_APPLY / LIVE_APPLY commit the batch and then CLEAR
        staging, so a second apply can't replay the same changes (a repeated live
        push would otherwise re-apply renames against already-renamed objects and
        fail).
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
            if out_path is None:
                return ApplyOutcome(mode=self.output_mode, ops=ops, out_path=None, detail=script)
            dest = Path(out_path)
            if (
                isinstance(self.source, OfflineSource)
                and dest.resolve() == self.source.path.resolve()
            ):
                raise PscError("output path must differ from the source config", ErrorType.CONFIG)
            # Trailing newline so the file is a well-formed script; staging is
            # deliberately kept (an exported script is a preview, not a commit).
            self._atomic_write(dest, script + "\n" if script else script)
            return ApplyOutcome(
                mode=self.output_mode, ops=ops, out_path=str(dest), detail=f"wrote {dest}"
            )

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
            if self.offline_partial:
                # Only the touched subtrees, in final state (#92). working_xml is
                # already the compounded apply of every staged changeset.
                payload = partial_config_from_batch(
                    self.working_xml, [s.changeset for s in self.staging]
                )
            else:
                payload = self.working_xml
            self._atomic_write(dest, payload)
            self.staging.clear()
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
        self.staging.clear()
        return ApplyOutcome(
            mode=self.output_mode, ops=ops, out_path=None, detail="pushed to candidate"
        )

    def export_kind(self, kind: ObjectKind, out_path: str) -> int:
        """Write every object of `kind` (whole config) to `out_path` as NDJSON.

        A read-only export, like `psc export`: it never touches staging or the
        working config. The same safety rail as offline apply holds — the
        destination must differ from an offline source export, so an export can
        never clobber the config it was taken from. Returns the object count.
        """
        lines = export_ndjson(self.working_snapshot, kind, scope=None)
        dest = Path(out_path)
        if isinstance(self.source, OfflineSource) and dest.resolve() == self.source.path.resolve():
            raise PscError("output path must differ from the source config", ErrorType.CONFIG)
        # Trailing newline so the file is a well-formed NDJSON stream (and empty
        # when there are no objects of this kind, not a lone newline).
        self._atomic_write(dest, "\n".join(lines) + "\n" if lines else "")
        return len(lines)

    @staticmethod
    def _atomic_write(dest: Path, text: str) -> None:
        """Write via a temp sibling + os.replace so `dest` is never truncated —
        a killed/failed write leaves the old file intact, not a half-written one."""
        tmp = dest.with_name(dest.name + ".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, dest)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise PscError(f"cannot write to {dest}: {exc}", ErrorType.INPUT) from exc
