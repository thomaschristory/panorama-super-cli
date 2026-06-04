"""`psc dedup` — find duplicate objects and merge them safely."""

from __future__ import annotations

import typer

from psc.cli._plan import OUT_FORMAT_OPTION, complete
from psc.cli.runtime import Runtime
from psc.core.dedup import (
    DuplicateGroup,
    ObjectRef,
    find_duplicate_addresses,
    find_duplicate_services,
    plan_merge,
)
from psc.core.refs import ReferenceGraph
from psc.core.source import ConfigFormat
from psc.output.errors import ErrorType, PscError
from psc.output.format import render

app = typer.Typer(no_args_is_help=True)


def _dup_rows(groups: list[DuplicateGroup]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for i, g in enumerate(groups, 1):
        for m in g.members:
            rows.append({"group": i, "value": g.value, "object": m.name, "location": m.location})
    return rows


@app.command("addresses")
def addresses(ctx: typer.Context) -> None:
    """List address objects that share an identical value under different names."""
    rt: Runtime = ctx.obj
    groups = find_duplicate_addresses(rt.snapshot())
    if rt.strict and not groups:
        raise PscError("no duplicate addresses", ErrorType.NOT_FOUND)
    render(
        rt.stdout,
        rt.output,
        model=groups,
        rows=_dup_rows(groups),
        table_title="duplicate addresses",
    )


@app.command("services")
def services(ctx: typer.Context) -> None:
    """List service objects that share an identical protocol/port definition."""
    rt: Runtime = ctx.obj
    groups = find_duplicate_services(rt.snapshot())
    if rt.strict and not groups:
        raise PscError("no duplicate services", ErrorType.NOT_FOUND)
    render(
        rt.stdout, rt.output, model=groups, rows=_dup_rows(groups), table_title="duplicate services"
    )


@app.command("merge")
def merge(
    ctx: typer.Context,
    keep: str = typer.Option(..., "--keep", help="Survivor object name."),
    remove: str = typer.Option(..., "--remove", help="Object to collapse into --keep and delete."),
    location: str | None = typer.Option(
        None, "--location", help="Location of both objects (default: --device-group or shared)."
    ),
    keep_location: str | None = typer.Option(None, "--keep-location"),
    remove_location: str | None = typer.Option(None, "--remove-location"),
    allow_value_change: bool = typer.Option(
        False, "--allow-value-change", help="Permit merging objects with different values."
    ),
    apply: bool = typer.Option(False, "--apply", help="Execute the merge (default: dry-run)."),
    out: str | None = typer.Option(
        None, "--out", help="Offline: write the rewritten config here (see --output-format)."
    ),
    output_format: ConfigFormat = OUT_FORMAT_OPTION,
) -> None:
    """Collapse one address object into another, repointing every reference.

    Dry-run by default: prints the rewrite plan (use `-o set` for the PAN-OS
    script). Repoints all groups/rules/NAT *before* deleting; refuses if any
    reference can't be safely repointed.
    """
    rt: Runtime = ctx.obj
    default_loc = location or rt.device_group or "shared"
    snap = rt.snapshot()
    graph = ReferenceGraph.build(snap)
    cs = plan_merge(
        snap,
        graph,
        keep=ObjectRef(name=keep, location=keep_location or default_loc),
        drop=ObjectRef(name=remove, location=remove_location or default_loc),
        allow_value_change=allow_value_change,
    )
    complete(rt, cs, apply=apply, out_path=out, out_format=output_format)
