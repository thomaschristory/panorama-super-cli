"""`psc version` — show the installed version and check PyPI for updates (#33).

`psc version` (bare) replaces `psc --version` with a format-aware report; the
`--version` flag stays for backwards compatibility. `psc version check` reaches
out to PyPI and tells you whether a newer release is available.
"""

from __future__ import annotations

import typer

from psc import __version__
from psc.cli.runtime import Runtime
from psc.core.version_check import check_for_update
from psc.output.format import render

app = typer.Typer(no_args_is_help=False)


@app.callback(invoke_without_command=True)
def version(ctx: typer.Context) -> None:
    """Show the installed psc version."""
    if ctx.invoked_subcommand is not None:
        return
    rt: Runtime = ctx.obj
    row = {"version": __version__}
    render(rt.stdout, rt.output, model=row, rows=[row], table_title="version")


@app.command("check")
def check(ctx: typer.Context) -> None:
    """Check PyPI for a newer release of panorama-super-cli."""
    rt: Runtime = ctx.obj
    info = check_for_update()
    render(rt.stdout, rt.output, model=info, rows=[info.model_dump()], table_title="update check")
    if info.update_available:
        rt.stderr.print(
            f"[yellow]update available[/yellow]: {info.installed} → {info.latest} "
            "(run: pip install -U panorama-super-cli)"
        )
    else:
        rt.stderr.print(f"[green]up to date[/green]: {info.installed}")
