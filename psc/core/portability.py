"""Bulk import/export of objects as NDJSON (newline-delimited JSON).

The counterpart to `crud`: where `crud` authors *one* object from typed CLI
flags, this authors *many* from serialized records — one JSON object per line.
It is the framework-free engine behind `psc export <kind>` and
`psc set <kind> -f <file>`.

Two directions, one shape:

- **Export** turns a `Snapshot`'s objects of one kind into a deterministic list
  of NDJSON lines (each the domain model's canonical `model_dump(mode="json")`,
  sorted by `(location, name)`), suitable for stdout, a file, or a diff.
- **Import** parses those records back and, *per record*, runs the exact same
  `crud` planner a single `set` would — composing every resulting `ObjectUpsert`
  (and every collision / type-change blocker, and every warning) into one
  combined `ChangeSet`. That combined plan then flows through the normal
  dry-run-default + `--apply` gate: import never writes objects directly, and a
  single blocker refuses the whole batch (repoint-before-delete safety, scaled
  to N objects).

Per-line failures are surfaced with the offending 1-based line number and
fail-fast: a malformed JSON line is an `INPUT` error, a record that fails crud
validation keeps crud's `VALIDATION` type. Failing fast (rather than collecting)
keeps a rejected import unambiguous — the operator fixes line N and re-runs.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from psc.core import crud
from psc.core.changeset import ChangeSet, ObjectKind
from psc.core.models import (
    Address,
    AddressGroup,
    Location,
    Service,
    ServiceGroup,
    Snapshot,
    Tag,
)
from psc.output.errors import ErrorType, PscError

# --- export ----------------------------------------------------------------


def _objects_for_kind(snapshot: Snapshot, kind: ObjectKind) -> list[Any]:
    by_kind: dict[ObjectKind, list[Any]] = {
        ObjectKind.ADDRESS: list(snapshot.addresses),
        ObjectKind.ADDRESS_GROUP: list(snapshot.address_groups),
        ObjectKind.SERVICE: list(snapshot.services),
        ObjectKind.SERVICE_GROUP: list(snapshot.service_groups),
        ObjectKind.TAG: list(snapshot.tags),
    }
    return by_kind[kind]


def export_ndjson(snapshot: Snapshot, kind: ObjectKind, *, scope: Location | None) -> list[str]:
    """Serialize every object of `kind` to a deterministic list of NDJSON lines.

    Each line is the domain model's canonical JSON (`model_dump(mode="json")`,
    stable field order). Objects are ordered by `(location, name)` so the output
    is byte-stable across runs (diff-friendly, round-trip-stable). `scope`
    restricts to the locations visible from a device-group (itself + ancestors +
    shared), consistent with the read commands; `None` exports every location.
    """
    visible = snapshot.visible_location_names(scope)
    objs = _objects_for_kind(snapshot, kind)
    selected = [o for o in objs if visible is None or o.location.name in visible]
    selected.sort(key=lambda o: (o.location.name, o.name))
    # `compact` separators keep one object per physical line; sort_keys is off
    # because Pydantic already emits a stable, model-defined field order.
    return [json.dumps(o.model_dump(mode="json"), ensure_ascii=False) for o in selected]


# --- import ----------------------------------------------------------------


def _location_of(record: dict[str, Any]) -> Location:
    """The `Location` a record targets (defaults to shared when absent).

    The serialized form is a bare string (`"shared"` / `"<dg>"`, see
    `Location._serialize`); `Location` also accepts that string form on parse.
    """
    raw = record.get("location", "shared")
    return Location.model_validate(raw)


def _plan_record(snapshot: Snapshot, record: dict[str, Any], kind: ObjectKind) -> ChangeSet:
    """Route one parsed record to the matching `crud` planner.

    This is the whole point of import: it reuses `crud` verbatim, so a bulk
    import validates and plans each object *identically* to a single `set`
    (same VALIDATION raises, same collision/type-change blockers).
    """
    location = _location_of(record)
    if kind is ObjectKind.ADDRESS:
        addr = Address.model_validate(record)
        return crud.plan_address(
            snapshot,
            addr.name,
            addr.type,
            addr.value,
            description=addr.description,
            tags=addr.tags,
            location=location,
        )
    if kind is ObjectKind.ADDRESS_GROUP:
        grp = AddressGroup.model_validate(record)
        return crud.plan_address_group(
            snapshot,
            grp.name,
            static_members=grp.static_members,
            dynamic_filter=grp.dynamic_filter,
            description=grp.description,
            tags=grp.tags,
            location=location,
        )
    if kind is ObjectKind.SERVICE:
        svc = Service.model_validate(record)
        return crud.plan_service(
            snapshot,
            svc.name,
            svc.protocol,
            destination_port=svc.destination_port,
            source_port=svc.source_port,
            description=svc.description,
            tags=svc.tags,
            location=location,
        )
    if kind is ObjectKind.SERVICE_GROUP:
        sg = ServiceGroup.model_validate(record)
        return crud.plan_service_group(
            snapshot, sg.name, sg.members, tags=sg.tags, location=location
        )
    tag = Tag.model_validate(record)
    return crud.plan_tag(
        snapshot, tag.name, color=tag.color, comments=tag.comments, location=location
    )


def _parse_line(line: str | dict[str, Any], line_no: int) -> dict[str, Any] | None:
    """Parse one NDJSON line into a record dict, or `None` to skip a blank line.

    A record may arrive already parsed (dict) — a web UI would call `plan_import`
    with dicts. A string is parsed with `json.loads`; a malformed one is an
    `INPUT` error naming the 1-based line number.
    """
    if isinstance(line, dict):
        return line
    if not line.strip():
        return None
    try:
        record = json.loads(line)
    except json.JSONDecodeError as exc:
        raise PscError(f"line {line_no}: not valid JSON ({exc.msg})", ErrorType.INPUT) from exc
    if not isinstance(record, dict):
        raise PscError(
            f"line {line_no}: expected a JSON object, got {type(record).__name__}",
            ErrorType.INPUT,
        )
    return record


def plan_import(
    snapshot: Snapshot,
    lines: Iterable[str | dict[str, Any]],
    kind: ObjectKind,
) -> ChangeSet:
    """Compose a single `ChangeSet` from an NDJSON stream of `kind` records.

    Each record is planned by the same `crud` planner a single `set` uses; the
    resulting upserts, blockers, and warnings are aggregated into one plan. The
    combined `blockers` hard-gate the whole import (cross-kind collisions and
    in-place type/mode changes each block), exactly as they do for one object.

    Fail-fast on the first bad line: a malformed JSON line raises `INPUT`, a
    record failing crud validation propagates crud's `VALIDATION` — either way
    the 1-based line number is included so the operator can fix and re-run.
    """
    combined = ChangeSet(title=f"import {kind.value} objects (NDJSON)")
    count = 0
    for idx, line in enumerate(lines, start=1):
        record = _parse_line(line, idx)
        if record is None:
            continue
        try:
            cs = _plan_record(snapshot, record, kind)
        except PscError as exc:
            # Re-stamp crud's message with the offending line so a bulk failure
            # is as locatable as a single-object one; keep its typed ErrorType.
            raise PscError(f"line {idx}: {exc.message}", exc.error_type) from exc
        # Aggregate every ChangeSet field, not just the ones crud emits today —
        # a future crud planner that produces renames/deletes must not have them
        # silently dropped from a bulk import.
        combined.upserts.extend(cs.upserts)
        combined.reference_edits.extend(cs.reference_edits)
        combined.rule_deletes.extend(cs.rule_deletes)
        combined.renames.extend(cs.renames)
        combined.deletes.extend(cs.deletes)
        combined.warnings.extend(cs.warnings)
        combined.blockers.extend(cs.blockers)
        count += 1
    combined.title = f"import {count} {kind.value} object(s) (NDJSON)"
    return combined
