"""`psc rule` — idempotent add/remove of a rule's reference-field members."""

from __future__ import annotations

import typer

from psc.cli._plan import OUT_FORMAT_OPTION, complete
from psc.cli.runtime import Runtime
from psc.core.models import Location, Rulebase
from psc.core.rule_edit import plan_rule_member_edit
from psc.core.source import ConfigFormat
from psc.output.errors import ErrorType, PscError

app = typer.Typer(no_args_is_help=True)


@app.command("edit-member")
def edit_member(
    ctx: typer.Context,
    rule: str = typer.Option(..., "--rule", help="Rule name to edit."),
    field: str = typer.Option(
        ..., "--field", help="Member-list field: source | destination | service | application."
    ),
    rulebase: str = typer.Option(
        "pre", "--rulebase", help="Which rulebase the rule sits in: pre | post."
    ),
    location: str | None = typer.Option(
        None,
        "--location",
        help="shared or a device-group (default: --device-group if set, else shared).",
    ),
    add: str | None = typer.Option(None, "--add", help="Member to add (idempotent)."),
    remove: str | None = typer.Option(None, "--remove", help="Member to remove (idempotent)."),
    apply: bool = typer.Option(False, "--apply", help="Execute the edit (default: dry-run)."),
    out: str | None = typer.Option(
        None,
        "--out",
        help="Write the plan artifact (set script or rewritten config) to this "
        "file; honoured even in a dry-run (see --output-format).",
    ),
    output_format: ConfigFormat = OUT_FORMAT_OPTION,
) -> None:
    """Add or remove one member of a rule field, idempotently.

    PAN-OS `set ... <field> [ x ]` *appends*, so this renders a delete-of-field
    plus a re-set of the full remaining list — re-running any op is a no-op.
    Exactly one of --add/--remove is required. Removing the last member of
    source/destination is accepted client-side, but the device may reject an
    empty field on commit.
    """
    rt: Runtime = ctx.obj
    if (add is None) == (remove is None):
        raise PscError("pass exactly one of --add or --remove", ErrorType.VALIDATION)
    try:
        rb = Rulebase(rulebase)
    except ValueError as exc:
        raise PscError(
            f"invalid --rulebase '{rulebase}' (choose pre or post)", ErrorType.VALIDATION
        ) from exc

    loc_name = location or rt.device_group or "shared"
    loc = Location.shared() if loc_name == "shared" else Location.dg(loc_name)

    cs = plan_rule_member_edit(rt.snapshot(), rule, loc, rb, field, add=add, remove=remove)
    complete(rt, cs, apply=apply, out_path=out, out_format=output_format)
