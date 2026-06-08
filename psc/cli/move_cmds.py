"""`psc move` — promote an object toward shared, reference-safely (issue #74).

A thin shell over `core.relocate.plan_move`: resolve the runtime source, build
the reference graph, plan the promotion, and hand the `ChangeSet` to the shared
dry-run/apply completion. All the safety logic lives in the engine.
"""

from __future__ import annotations

import typer

from psc.cli._plan import OUT_FORMAT_OPTION, complete
from psc.cli.runtime import Runtime
from psc.core.changeset import ObjectKind
from psc.core.refs import ReferenceGraph
from psc.core.relocate import plan_move
from psc.core.source import ConfigFormat


def move(
    ctx: typer.Context,
    kind: ObjectKind = typer.Argument(
        ..., help="Object kind: address, address-group, service, service-group, or tag."
    ),
    name: str = typer.Argument(..., help="Name of the object to move."),
    from_location: str = typer.Option(
        ..., "--from", help="Current location: 'shared' or a device-group name."
    ),
    to_location: str = typer.Option(
        ...,
        "--to",
        help="Destination: 'shared' or an ancestor device-group of --from. "
        "move only promotes toward shared.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Execute the move (default: dry-run)."),
    out: str | None = typer.Option(
        None,
        "--out",
        help="Write the plan artifact (set script or rewritten config) to this file.",
    ),
    output_format: ConfigFormat = OUT_FORMAT_OPTION,
) -> None:
    """Promote an object from a device-group toward `shared`.

    A move is create-at-destination + delete-at-source. It is restricted to the
    safe direction — `--to` must be `shared` or an *ancestor* of `--from` — so
    references fall through to the destination without any repoint. Dry-run by
    default; refuses (exit 6) if the move would orphan references, hit a
    shadowing device-group between source and destination, leave the object's
    own dependencies unresolved at the destination, or collide with a
    different-valued object already at the destination.
    """
    rt: Runtime = ctx.obj
    snap = rt.snapshot()
    graph = ReferenceGraph.build(snap)
    cs = plan_move(
        snap,
        graph,
        kind=kind,
        name=name,
        source_name=from_location,
        dest_name=to_location,
    )
    complete(rt, cs, apply=apply, out_path=out, out_format=output_format)
