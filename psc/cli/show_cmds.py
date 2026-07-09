"""`psc show <name>` — open an object and show what it contains.

A convenience alias for `psc find object <name> --expand`: draws the member
tree and effective leaf set over the same `inspect_object` engine.
"""

from __future__ import annotations

import typer

from psc.cli._inspect_render import render_object_views
from psc.cli.runtime import Runtime
from psc.core.inspect import inspect_object
from psc.output.errors import ErrorType, PscError


def show(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Exact object name to open."),
) -> None:
    """Open an object (address, group, service, service-group, tag, or rule) and
    show its member tree plus the effective leaf addresses/ports it resolves to.

    Equivalent to `psc find object <name> --expand`. Dynamic filters, dangling
    members and cycles are shown and flagged.
    """
    rt: Runtime = ctx.obj
    views = inspect_object(rt.snapshot(), name, scope=rt.scope())
    if rt.strict and not views:
        raise PscError(f"no object named '{name}'", ErrorType.NOT_FOUND)
    render_object_views(rt.stdout, rt.stderr, rt.output, views)
