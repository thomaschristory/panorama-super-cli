"""`psc diff` — compare two configs, or two device-groups in one config.

Read-only drift report. Two modes, mutually exclusive:

- ``psc diff a.xml b.xml`` — object/group/rule differences between two exported
  configs (pre/post-change review).
- ``psc diff --device-group A --against B`` — differences between the *effective
  visible object sets* of two device-groups in the single loaded config.

Differences are *data*, not an error: the command exits 0 even when the two
sides differ. Only real failures (bad args, unreadable file) raise a
``PscError``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from psc.cli.runtime import Runtime
from psc.core.diff import KindDiff, SnapshotDiff, diff_snapshots
from psc.core.models import Location, Snapshot
from psc.core.parse import parse_config
from psc.output.errors import ErrorType, PscError
from psc.output.format import render

# (attribute on SnapshotDiff, human label) — one row block per kind, in a stable
# order so table/csv output is deterministic.
_KINDS: tuple[tuple[str, str], ...] = (
    ("addresses", "address"),
    ("address_groups", "address-group"),
    ("services", "service"),
    ("service_groups", "service-group"),
    ("tags", "tag"),
    ("security_rules", "security-rule"),
    ("nat_rules", "nat-rule"),
)


def _load(path: Path) -> Snapshot:
    """Parse one config file into a Snapshot, mapping IO/parse failures onto the
    error contract (an unreadable or malformed export is an INPUT failure)."""
    try:
        xml = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PscError(f"cannot read {path}: {exc}", ErrorType.INPUT) from exc
    try:
        return parse_config(xml)
    except Exception as exc:  # defusedxml/parse errors → typed INPUT
        raise PscError(f"failed to parse {path}: {exc}", ErrorType.INPUT) from exc


def _changed_detail(before: dict[str, Any], after: dict[str, Any]) -> str:
    """A terse `field: before -> after` summary of what differs, for the table."""
    keys = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    return "; ".join(f"{k}: {before.get(k)!r} -> {after.get(k)!r}" for k in keys)


def _rows(diff: SnapshotDiff) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for attr, label in _KINDS:
        kd: KindDiff[Any] = getattr(diff, attr)
        for obj in kd.added:
            rows.append(
                {
                    "kind": label,
                    "change": "added",
                    "name": obj.name,
                    "location": obj.location.name,
                    "detail": "",
                }
            )
        for obj in kd.removed:
            rows.append(
                {
                    "kind": label,
                    "change": "removed",
                    "name": obj.name,
                    "location": obj.location.name,
                    "detail": "",
                }
            )
        for ch in kd.changed:
            rows.append(
                {
                    "kind": label,
                    "change": "changed",
                    "name": ch.name,
                    "location": ch.location,
                    "detail": _changed_detail(ch.before, ch.after),
                }
            )
    return rows


def diff(
    ctx: typer.Context,
    a: str | None = typer.Argument(None, help="First config XML (file-vs-file mode)."),
    b: str | None = typer.Argument(None, help="Second config XML (file-vs-file mode)."),
    device_group: str | None = typer.Option(
        None,
        "--device-group",
        help="DG-vs-DG mode: the base device-group in the loaded config.",
    ),
    against: str | None = typer.Option(
        None,
        "--against",
        help="DG-vs-DG mode: the device-group to compare the base against.",
    ),
) -> None:
    """Compare two configs (``a.xml b.xml``) or two device-groups
    (``--device-group A --against B``) and report added/removed/changed objects,
    groups, and rules. Read-only: exits 0 even when differences are found."""
    rt: Runtime = ctx.obj

    file_mode = a is not None or b is not None
    dg_mode = device_group is not None or against is not None

    if file_mode and dg_mode:
        raise PscError(
            "choose one mode: two config files, OR --device-group/--against — not both",
            ErrorType.VALIDATION,
        )
    if not file_mode and not dg_mode:
        raise PscError(
            "provide two config files (a.xml b.xml) or --device-group A --against B",
            ErrorType.VALIDATION,
        )

    if dg_mode:
        if device_group is None or against is None:
            raise PscError("DG mode needs both --device-group and --against", ErrorType.VALIDATION)
        snap = rt.snapshot()
        result = diff_snapshots(
            snap,
            snap,
            scope_base=Location.dg(device_group),
            scope_other=Location.dg(against),
        )
        title = f"diff {device_group} -> {against}"
    else:
        if a is None or b is None:
            raise PscError("file mode needs two config paths (a.xml b.xml)", ErrorType.VALIDATION)
        base, other = _load(Path(a)), _load(Path(b))
        result = diff_snapshots(base, other)
        title = f"diff {a} -> {b}"

    render(
        rt.stdout,
        rt.output,
        model=result,
        rows=_rows(result),
        table_title=title,
        group_by="kind",
    )
