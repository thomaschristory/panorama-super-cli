"""`psc refs` — where-used, unused, and dangling references."""

from __future__ import annotations

import typer

from psc.cli.runtime import Runtime
from psc.core.models import Location
from psc.core.refs import ReferenceGraph
from psc.core.resolve import find_object
from psc.output.errors import ErrorType, PscError
from psc.output.format import render

app = typer.Typer(no_args_is_help=True)

_KINDS = ("address", "address-group", "service", "service-group", "tag")


@app.command("used")
def used(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Object name to trace."),
    kind: str | None = typer.Option(None, "--kind", help=f"One of: {', '.join(_KINDS)}."),
    location: str | None = typer.Option(None, "--location", help="shared or a device-group."),
) -> None:
    """List every reference that resolves to a given object (the delete/rename pre-flight)."""
    rt: Runtime = ctx.obj
    snap = rt.snapshot()
    graph = ReferenceGraph.build(snap)

    if kind is None or location is None:
        hits = find_object(snap, name)
        if not hits:
            raise PscError(f"no object named '{name}'", ErrorType.NOT_FOUND)
        if len(hits) > 1 and (kind is None or location is None):
            raise PscError(
                f"'{name}' is ambiguous ({len(hits)} objects); pass --kind and --location",
                ErrorType.VALIDATION,
                details={"candidates": [{"kind": h.kind, "location": h.location} for h in hits]},
            )
        kind = kind or hits[0].kind
        location = location or hits[0].location

    loc = Location.shared() if location == "shared" else Location.dg(location)
    refs = graph.where_used(kind, name, loc)
    rows = [
        {
            "referrer_kind": r.referrer_kind,
            "referrer": r.referrer_name,
            "location": r.referrer_location.name,
            "rulebase": r.rulebase.value if r.rulebase else "",
            "field": r.field,
        }
        for r in refs
    ]
    if rt.strict and not refs:
        raise PscError(f"'{name}' is unused", ErrorType.NOT_FOUND)
    render(rt.stdout, rt.output, model=refs, rows=rows, table_title=f"where '{name}' is used")


@app.command("unused")
def unused(
    ctx: typer.Context,
    kind: str = typer.Option("address", "--kind", help=f"One of: {', '.join(_KINDS)}."),
) -> None:
    """List objects no rule reaches — directly or transitively through groups."""
    rt: Runtime = ctx.obj
    graph = ReferenceGraph.build(rt.snapshot())
    targets = graph.unused(kind)
    rows = [{"kind": t.kind, "name": t.name, "location": t.location.name} for t in targets]
    model = [{"kind": t.kind, "name": t.name, "location": t.location.name} for t in targets]
    if rt.strict and not targets:
        raise PscError(f"no unused {kind}", ErrorType.NOT_FOUND)
    render(rt.stdout, rt.output, model=model, rows=rows, table_title=f"unused {kind}")
    if targets:
        # `unused` only sees device-group objects + policy rulebases. Objects
        # referenced from templates/network config, NAT-rule tags, or matched
        # into a dynamic address group are NOT scanned and look unused here. Warn
        # on stderr so stdout stays pure machine output (#56).
        rt.stderr.print(
            "[yellow]caveat[/yellow]: candidates only — these are unreferenced by the "
            "scanned objects/policy rulebases. NOT scanned: templates & network/device "
            "config, dynamic-address-group membership. Verify before deleting (esp. "
            "shared). See docs: Coverage and blind spots.",
            soft_wrap=True,
            highlight=False,
        )


@app.command("dangling")
def dangling(ctx: typer.Context) -> None:
    """List references that point at names that don't resolve to any object."""
    rt: Runtime = ctx.obj
    graph = ReferenceGraph.build(rt.snapshot())
    refs = graph.dangling()
    rows = [
        {
            "referrer_kind": r.referrer_kind,
            "referrer": r.referrer_name,
            "location": r.referrer_location.name,
            "field": r.field,
            "missing": r.target_name,
        }
        for r in refs
    ]
    if rt.strict and refs:
        raise PscError(f"{len(refs)} dangling references", ErrorType.CONFLICT)
    render(rt.stdout, rt.output, model=refs, rows=rows, table_title="dangling references")
