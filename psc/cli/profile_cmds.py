"""`psc profile` — manage live Panorama connection profiles."""

from __future__ import annotations

import typer

from psc.cli.runtime import Runtime
from psc.config.loader import config_path, save_config
from psc.config.models import Profile
from psc.output.errors import ErrorType, PscError
from psc.output.format import render

app = typer.Typer(no_args_is_help=True)


@app.command("list")
def list_profiles(ctx: typer.Context) -> None:
    """Show configured live profiles (API keys are not printed)."""
    rt: Runtime = ctx.obj
    # Where the config lives is platform-dependent (it differs on Windows), so
    # surface it to help users find/edit the file (#48). It goes to stderr so it
    # never pollutes the machine-readable rows on stdout, and prints even when
    # no profiles are configured yet — the empty case is exactly when "where do
    # I put them?" matters most.
    path = config_path()
    suffix = "" if path.exists() else " (not created yet)"
    # soft_wrap + markup=False: never wrap a long path onto two lines, and never
    # let a path with `[...]` be parsed as rich markup.
    rt.stderr.print(f"config file: {path}{suffix}", soft_wrap=True, markup=False, highlight=False)
    rows = [
        {
            "name": p.name,
            "hostname": p.hostname,
            "port": p.port,
            "device_group": p.device_group or "",
            "default": p.name == rt.config.default_profile,
        }
        for p in rt.config.profiles
    ]
    render(rt.stdout, rt.output, model=rows, rows=rows, table_title="profiles")


@app.command("add")
def add(
    ctx: typer.Context,
    name: str = typer.Option(..., "--name"),
    hostname: str = typer.Option(..., "--host"),
    api_key: str = typer.Option("", "--api-key", help="Panorama API key (stored 0600)."),
    port: int = typer.Option(443, "--port"),
    device_group: str | None = typer.Option(None, "--device-group"),
    insecure: bool = typer.Option(
        False, "--insecure", help="Skip TLS certificate verification (self-signed Panorama)."
    ),
    set_default: bool = typer.Option(False, "--default", help="Make this the default profile."),
) -> None:
    """Add or replace a live profile in ~/.psc/config.yaml."""
    rt: Runtime = ctx.obj
    cfg = rt.config
    if any(p.name == name for p in cfg.profiles):
        cfg.profiles = [p for p in cfg.profiles if p.name != name]
    cfg.profiles.append(
        Profile(
            name=name,
            hostname=hostname,
            api_key=api_key,
            port=port,
            verify_ssl=not insecure,
            device_group=device_group,
        )
    )
    if set_default or cfg.default_profile is None:
        cfg.default_profile = name
    path = save_config(cfg)
    rt.stderr.print(f"[green]saved[/green] profile '{name}' → {path}")


@app.command("remove")
def remove(ctx: typer.Context, name: str = typer.Argument(...)) -> None:
    """Remove a live profile."""
    rt: Runtime = ctx.obj
    cfg = rt.config
    if not any(p.name == name for p in cfg.profiles):
        raise PscError(f"no profile named '{name}'", ErrorType.NOT_FOUND)
    cfg.profiles = [p for p in cfg.profiles if p.name != name]
    if cfg.default_profile == name:
        cfg.default_profile = cfg.profiles[0].name if cfg.profiles else None
    save_config(cfg)
    rt.stderr.print(f"[green]removed[/green] profile '{name}'")
