"""`psc name` — naming-template lint and reference-aware rename (opt-in)."""

from __future__ import annotations

import typer

from psc.cli._options import OUT_OPTION
from psc.cli._plan import OUT_FORMAT_OPTION, complete
from psc.cli.runtime import Runtime
from psc.core.changeset import ObjectKind
from psc.core.models import Location
from psc.core.naming import lint as lint_engine
from psc.core.naming import plan_apply_scheme, plan_rename
from psc.core.refs import ReferenceGraph
from psc.core.source import ConfigFormat
from psc.output.errors import ErrorType, PscError
from psc.output.format import render

app = typer.Typer(no_args_is_help=True)


@app.command("lint")
def lint(
    ctx: typer.Context,
    show_all: bool = typer.Option(False, "--all", help="Include already-compliant objects."),
) -> None:
    """Report objects whose name drifts from the configured naming scheme."""
    rt: Runtime = ctx.obj
    findings = lint_engine(rt.snapshot(), rt.config.defaults.naming)
    if not show_all:
        findings = [f for f in findings if not f.compliant]
    rows = [
        {
            "kind": f.kind,
            "location": f.location,
            "current": f.current,
            "suggested": f.suggested,
            "compliant": f.compliant,
        }
        for f in findings
    ]
    if rt.strict and any(not f.compliant for f in findings):
        raise PscError("naming drift found", ErrorType.CONFLICT)
    render(rt.stdout, rt.output, model=findings, rows=rows, table_title="naming drift")


@app.command("rename")
def rename(
    ctx: typer.Context,
    object_name: str = typer.Option(..., "--object", help="Current object name."),
    to: str = typer.Option(..., "--to", help="New name."),
    kind: ObjectKind = typer.Option(ObjectKind.ADDRESS, "--kind"),
    location: str | None = typer.Option(None, "--location"),
    apply: bool = typer.Option(False, "--apply", help="Execute the rename (default: dry-run)."),
    out: str | None = OUT_OPTION,
    output_format: ConfigFormat = OUT_FORMAT_OPTION,
) -> None:
    """Rename one object, repointing every reference (refuses on shadow collisions)."""
    rt: Runtime = ctx.obj
    loc = location or rt.device_group or "shared"
    snap = rt.snapshot()
    graph = ReferenceGraph.build(snap)
    cs = plan_rename(snap, graph, kind=kind, location_name=loc, old_name=object_name, new_name=to)
    complete(rt, cs, apply=apply, out_path=out, out_format=output_format)


@app.command("apply")
def apply_scheme(
    ctx: typer.Context,
    object_name: str | None = typer.Option(
        None, "--object", help="Object to rename to its scheme name."
    ),
    rename_all: bool = typer.Option(
        False, "--all", help="Rename EVERY non-compliant object to its scheme name in one plan."
    ),
    location: str | None = typer.Option(None, "--location"),
    apply: bool = typer.Option(False, "--apply", help="Execute the rename (default: dry-run)."),
    out: str | None = OUT_OPTION,
    output_format: ConfigFormat = OUT_FORMAT_OPTION,
) -> None:
    """Rename object(s) to the name the configured scheme implies.

    `--object` renames one object; `--all` renames every non-compliant object
    (from `name lint`) in a single reviewed plan, blocking any that would collide
    or shadow. Both are dry-run by default; pass `--apply` to execute.
    """
    rt: Runtime = ctx.obj
    scheme = rt.config.defaults.naming
    snap = rt.snapshot()

    if rename_all == (object_name is not None):
        raise PscError(
            "pass exactly one of --object or --all",
            ErrorType.VALIDATION,
        )

    if rename_all:
        scope: Location | None = None
        if location is not None:
            scope = Location.shared() if location == "shared" else Location.dg(location)
        elif rt.device_group is not None:
            scope = Location.dg(rt.device_group)
        graph = ReferenceGraph.build(snap)
        cs = plan_apply_scheme(snap, graph, scheme, scope=scope)
        complete(rt, cs, apply=apply, out_path=out, out_format=output_format)
        return

    assert object_name is not None  # narrowed by the exactly-one guard above
    loc = location or rt.device_group or "shared"

    suggested: str | None = None
    kind = ObjectKind.ADDRESS
    for a in snap.addresses:
        if a.name == object_name and a.location.name == loc:
            suggested, kind = scheme.address_name(a), ObjectKind.ADDRESS
            break
    else:
        for s in snap.services:
            if s.name == object_name and s.location.name == loc:
                suggested, kind = scheme.service_name(s), ObjectKind.SERVICE
                break
    if suggested is None:
        raise PscError(
            f"cannot derive a scheme name for '{object_name}' @{loc} "
            "(not found, or value kind has no template)",
            ErrorType.VALIDATION,
        )
    if suggested == object_name:
        rt.stderr.print("[green]already compliant[/green]")
        return
    graph = ReferenceGraph.build(snap)
    cs = plan_rename(
        snap, graph, kind=kind, location_name=loc, old_name=object_name, new_name=suggested
    )
    complete(rt, cs, apply=apply, out_path=out, out_format=output_format)
