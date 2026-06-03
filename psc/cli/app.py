"""`psc` entry point: global options, the typed error contract, command wiring."""

from __future__ import annotations

import json

import click
import typer
from rich.console import Console

from psc import __version__
from psc.cli import dedup_cmds, find_cmds, name_cmds, profile_cmds, refs_cmds
from psc.cli.runtime import Runtime, configure_logging
from psc.config.loader import load_config
from psc.output.errors import PscError
from psc.output.format import OutputFormat

app = typer.Typer(
    add_completion=True,
    no_args_is_help=True,
    rich_markup_mode="rich",
    help="Agent-friendly Palo Alto Panorama object management — find, dedup, "
    "merge, rename, and audit address/service objects. Dry-run by default.",
)

# Set by the root callback so the top-level error handler can honour -o json.
_ACTIVE: Runtime | None = None


def _version_cb(value: bool) -> None:
    if value:
        Console().print(f"psc {__version__}")
        raise typer.Exit


@app.callback()
def root(
    ctx: typer.Context,
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Offline: path to an exported Panorama config XML.",
        rich_help_panel="Source",
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        "-p",
        help="Live: named profile from ~/.psc/config.yaml.",
        rich_help_panel="Source",
    ),
    output: OutputFormat | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Output format (default: table on a TTY, json when piped).",
        rich_help_panel="Output",
    ),
    device_group: str | None = typer.Option(
        None,
        "--device-group",
        "-d",
        help="Scope to one device-group (plus inherited shared).",
        rich_help_panel="Source",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Exit non-zero when a lookup finds nothing.",
        rich_help_panel="Safety",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Verbose structured logs on stderr.",
        rich_help_panel="Output",
    ),
    _version: bool = typer.Option(
        False,
        "--version",
        callback=_version_cb,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    global _ACTIVE  # noqa: PLW0603 — single process-wide handle for the error printer
    configure_logging(debug)
    rt = Runtime(
        config=load_config(),
        config_file=config,
        profile=profile,
        debug=debug,
        device_group=device_group,
        strict=strict,
        _output=output,
    )
    ctx.obj = rt
    _ACTIVE = rt


app.add_typer(find_cmds.app, name="find", help="Find objects by IP/value/name.")
app.add_typer(dedup_cmds.app, name="dedup", help="Find and merge duplicate objects.")
app.add_typer(refs_cmds.app, name="refs", help="Where-used, unused, and dangling references.")
app.add_typer(name_cmds.app, name="name", help="Naming-template lint and rename.")
app.add_typer(profile_cmds.app, name="profile", help="Manage live connection profiles.")


def _emit_error(err: PscError) -> None:
    envelope = json.dumps(err.envelope(), ensure_ascii=False)
    if _ACTIVE is not None and _ACTIVE.output is OutputFormat.JSON:
        _ACTIVE.stdout.print(envelope, markup=False, highlight=False, soft_wrap=True)
    else:
        err_console = Console(stderr=True)
        err_console.print(f"[red]error[/red]: {err.message}", highlight=False)
        err_console.print(envelope, markup=False, highlight=False, soft_wrap=True)


def main() -> None:
    try:
        app(standalone_mode=False)
    except PscError as err:
        _emit_error(err)
        raise SystemExit(err.exit_code) from None
    except click.UsageError as err:
        err.show()
        raise SystemExit(2) from None
    except (click.exceptions.Abort, KeyboardInterrupt):
        Console(stderr=True).print("aborted")
        raise SystemExit(130) from None
    except click.exceptions.Exit as err:
        raise SystemExit(err.exit_code) from None


if __name__ == "__main__":
    main()
