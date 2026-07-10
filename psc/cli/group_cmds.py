"""`psc group` — idempotent add/remove of an address-/service-group's members."""

from __future__ import annotations

import typer

from psc.cli._options import OUT_OPTION, location_from_name
from psc.cli._plan import OUT_FORMAT_OPTION, complete
from psc.cli.runtime import Runtime
from psc.core.group_edit import plan_group_member_edit
from psc.core.source import ConfigFormat
from psc.output.errors import ErrorType, PscError

app = typer.Typer(no_args_is_help=True)


@app.command("edit-member")
def edit_member(
    ctx: typer.Context,
    group: str = typer.Option(..., "--group", help="Group name to edit."),
    kind: str | None = typer.Option(
        None,
        "--kind",
        help="Disambiguate a name that is both kinds: address-group | service-group.",
    ),
    location: str | None = typer.Option(
        None,
        "--location",
        help="shared or a device-group (default: --device-group if set, else shared).",
    ),
    add: str | None = typer.Option(None, "--add", help="Member to add (idempotent)."),
    remove: str | None = typer.Option(None, "--remove", help="Member to remove (idempotent)."),
    apply: bool = typer.Option(False, "--apply", help="Execute the edit (default: dry-run)."),
    out: str | None = OUT_OPTION,
    output_format: ConfigFormat = OUT_FORMAT_OPTION,
) -> None:
    """Add or remove one member of an address-group or service-group, idempotently.

    PAN-OS `set … static [ x ]` *appends*, so this renders a delete-of-field plus
    a re-set of the full remaining list — re-running any op is a no-op. Exactly
    one of --add/--remove is required. A dynamic (filter-based) address-group has
    no static member list and is rejected.
    """
    rt: Runtime = ctx.obj
    if (add is None) == (remove is None):
        raise PscError("pass exactly one of --add or --remove", ErrorType.VALIDATION)

    loc = location_from_name(location or rt.device_group or "shared")
    cs = plan_group_member_edit(rt.snapshot(), group, loc, add=add, remove=remove, kind=kind)
    complete(rt, cs, apply=apply, out_path=out, out_format=output_format)
