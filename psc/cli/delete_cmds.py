"""`psc delete` — reference-safe deletion of objects named on the command line.

This command completes the `refs unused` workflow. `unused` finds the cleanup
candidates; this command turns them into a plan. The two compose over a pipe,
so a script can filter the list before anything is deleted:

    psc -c cfg.xml -o jsonl refs unused --kind address --no-caveat \\
      | jq -c 'select(.tags | length == 0)' \\
      | psc -c cfg.xml delete -f -

A target is written `[kind:]name[@location]`. The kind defaults to `--kind`
(`address`). When the location is absent the command resolves the name across
the config: one match wins, several make it stop and list them, because a
delete that guesses the location is a delete in the wrong device group.

`-f/--file` also reads the machine output of `refs unused` directly, as JSON
lines or as one JSON array, so no `jq` step is needed for the simple case.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from typing import Any

import typer

from psc.cli._options import location_from_name
from psc.cli._plan import OUT_FORMAT_OPTION, complete
from psc.cli.runtime import Runtime
from psc.core.models import Location, Snapshot
from psc.core.purge import PURGE_KINDS, plan_purge
from psc.core.refs import ReferenceGraph, Target
from psc.core.source import ConfigFormat
from psc.output.errors import ErrorType, PscError

_STDIN = "-"


def _locations_of(snapshot: Snapshot, kind: str, name: str) -> list[str]:
    """Every location that defines `name` as an object of `kind`."""
    by_kind: dict[str, Sequence[Any]] = {
        "address": snapshot.addresses,
        "address-group": snapshot.address_groups,
        "service": snapshot.services,
        "service-group": snapshot.service_groups,
        "tag": snapshot.tags,
    }
    return [o.location.name for o in by_kind[kind] if o.name == name]


def parse_spec(
    spec: str, *, default_kind: str, default_location: str | None
) -> tuple[str, str, str | None]:
    """Split `[kind:]name[@location]` into its three parts.

    The location stays `None` when the spec omits it and no default applies;
    `resolve_target` then looks the name up in the config.
    """
    text = spec.strip()
    if not text:
        raise PscError("empty target", ErrorType.VALIDATION)
    kind = default_kind
    if ":" in text:
        kind, _, text = text.partition(":")
        kind = kind.strip()
    location = default_location
    if "@" in text:
        text, _, location = text.partition("@")
        location = location.strip()
    name = text.strip()
    if not name:
        raise PscError(f"target '{spec}' names no object", ErrorType.VALIDATION)
    if kind not in PURGE_KINDS:
        raise PscError(
            f"'{kind}' is not a deletable kind (choose one of {', '.join(PURGE_KINDS)})",
            ErrorType.VALIDATION,
        )
    return kind, name, location


def resolve_target(snapshot: Snapshot, kind: str, name: str, location: str | None) -> Target:
    """Bind `(kind, name, location)` to one object, or say why it cannot.

    A spec without a location is resolved by search rather than by guessing a
    default: the same name can exist in shared and in several device groups, and
    deleting the wrong one is not recoverable from the plan alone.
    """
    if location is not None:
        return Target(kind=kind, name=name, location=location_from_name(location))
    hits = _locations_of(snapshot, kind, name)
    if not hits:
        # Let the engine report it as a blocker, so a bad name in a long list
        # does not hide the rest of the plan behind a usage error.
        return Target(kind=kind, name=name, location=Location.shared())
    if len(hits) > 1:
        raise PscError(
            f"{kind} '{name}' exists in {len(hits)} locations — qualify it as "
            f"'{kind}:{name}@<location>'",
            ErrorType.VALIDATION,
            details={"candidates": sorted(hits)},
        )
    return Target(kind=kind, name=name, location=location_from_name(hits[0]))


def _spec_from_row(row: dict[str, Any]) -> str:
    """A `kind:name@location` spec from one `refs unused` output row."""
    try:
        return f"{row['kind']}:{row['name']}@{row['location']}"
    except KeyError as exc:
        raise PscError(
            f"row is missing the {exc} field — expected `refs unused` output",
            ErrorType.INPUT,
        ) from exc


def specs_from_text(text: str) -> list[str]:
    """Target specs from a file or from stdin.

    Three shapes are accepted so the machine output of `refs unused` works
    unchanged: one JSON array (`-o json`), one JSON object per line (`-o jsonl`),
    and plain `[kind:]name[@location]` lines with `#` comments.
    """
    stripped = text.strip()
    if stripped.startswith("["):
        try:
            rows = json.loads(stripped)
        except ValueError as exc:
            raise PscError(f"cannot parse JSON input: {exc}", ErrorType.INPUT) from exc
        return [_spec_from_row(r) for r in rows]
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("{"):
            try:
                out.append(_spec_from_row(json.loads(line)))
            except ValueError as exc:
                raise PscError(f"cannot parse JSON line: {exc}", ErrorType.INPUT) from exc
        else:
            out.append(line)
    return out


def _read_targets_file(path: str) -> list[str]:
    if path == _STDIN:
        return specs_from_text(sys.stdin.read())
    try:
        with open(path, encoding="utf-8") as handle:
            return specs_from_text(handle.read())
    except OSError as exc:
        raise PscError(f"cannot read {path}: {exc}", ErrorType.INPUT) from exc


def delete(
    ctx: typer.Context,
    targets: list[str] | None = typer.Argument(
        None, help="Object to delete: [kind:]name[@location] (repeatable)."
    ),
    target: list[str] | None = typer.Option(
        None, "--target", help="Additional target (repeatable); same as a positional arg."
    ),
    file: str | None = typer.Option(
        None,
        "--file",
        "-f",
        help="Read targets from a file, or '-' for stdin. Accepts plain "
        "[kind:]name[@location] lines (# comments) and the JSON/JSONL output of "
        "`refs unused`.",
    ),
    kind: str = typer.Option(
        "address",
        "--kind",
        help=f"Kind for a target that omits one. One of: {', '.join(PURGE_KINDS)}.",
    ),
    location: str | None = typer.Option(
        None,
        "--location",
        help="Location for a target that omits one. Default: look the name up in "
        "the config and refuse when it is ambiguous.",
    ),
    keep_groups: bool = typer.Option(
        False,
        "--keep-groups",
        help="Scrub group/rule member fields but delete neither groups nor objects.",
    ),
    keep_rules: bool = typer.Option(
        False,
        "--keep-rules",
        help="Keep a rule left with an empty required field instead of deleting it.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Execute the deletion (default: dry-run)."),
    out: str | None = typer.Option(
        None,
        "--out",
        help="Write the plan artifact (set script or rewritten config) to this file.",
    ),
    output_format: ConfigFormat = OUT_FORMAT_OPTION,
) -> None:
    """Delete named objects, reference-safe: scrub every reference, then remove.

    Takes the five kinds `refs unused` reports — address, address-group,
    service, service-group and tag. The plan scrubs every group member list and
    rule field that names the object, deletes a rule left with an empty required
    field, deletes a group the scrub empties, and removes the objects last. It
    refuses (exit 6) when a reference cannot be rewritten: a NAT translation, a
    PBF next hop, a dynamic address-group filter that selects the object by tag,
    or an object's own tag list.

    `unused` finds candidates; it does not prove they are safe to delete. Read
    the warnings, and verify a shared or tagged candidate in Panorama first.
    """
    rt: Runtime = ctx.obj
    specs = list(targets or []) + list(target or [])
    if file:
        specs += _read_targets_file(file)
    if not specs:
        raise PscError(
            "provide one or more targets ([kind:]name[@location]), or --file",
            ErrorType.VALIDATION,
        )

    snap = rt.snapshot()
    default_location = location if location is not None else _scope_name(rt)
    resolved = [
        resolve_target(snap, *parse_spec(s, default_kind=kind, default_location=default_location))
        for s in specs
    ]

    graph = ReferenceGraph.build(snap)
    cs = plan_purge(snap, graph, resolved, keep_groups=keep_groups, keep_rules=keep_rules)
    complete(rt, cs, apply=apply, out_path=out, out_format=output_format)


def _scope_name(rt: Runtime) -> str | None:
    """The global `--device-group` as a location name, or `None`."""
    scope = rt.scope()
    return None if scope is None else scope.name
