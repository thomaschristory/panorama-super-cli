"""`psc` entry point: global options, the typed error contract, command wiring."""

from __future__ import annotations

import json
from types import ModuleType

import click
import typer
from rich.console import Console

from psc import __version__
from psc.cli import (
    audit_cmds,
    auth_cmds,
    dedup_cmds,
    find_cmds,
    name_cmds,
    profile_cmds,
    refs_cmds,
    rule_cmds,
    set_cmds,
    version_cmds,
)
from psc.cli.runtime import Runtime, configure_logging
from psc.config.loader import config_path, load_config
from psc.output.errors import PscError
from psc.output.format import OutputFormat


def _click_exception_module() -> ModuleType:
    """The Click `exceptions` module whose flavour matches what `app(...)` raises.

    Typer >=0.16 vendors its own Click under `typer._click`, so the exceptions
    `app(standalone_mode=False)` raises are not subclasses of the real `click.*`
    (issue #31). Older Typer has no `typer._click` at all — importing it at
    module top level would crash psc on *import*, breaking every command, which
    is strictly worse than the bug we're fixing. Resolve defensively: prefer the
    vendored module, fall back to the real Click (which older Typer raises).
    """
    try:
        from typer._click import exceptions as vendored  # noqa: PLC0415 — conditional
    except ImportError:
        from click import exceptions as real  # noqa: PLC0415 — fallback for older Typer

        return real
    return vendored


_typer_click_exc = _click_exception_module()

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
    cfg_path = config_path()  # resolve once so the loaded and displayed paths match
    rt = Runtime(
        config=load_config(cfg_path),
        config_file=config,
        profile=profile,
        debug=debug,
        device_group=device_group,
        strict=strict,
        _output=output,
        config_path=cfg_path,
    )
    ctx.obj = rt
    _ACTIVE = rt


app.add_typer(find_cmds.app, name="find", help="Find objects by IP/value/name.")
app.add_typer(dedup_cmds.app, name="dedup", help="Find and merge duplicate objects.")
app.add_typer(refs_cmds.app, name="refs", help="Where-used, unused, and dangling references.")
app.add_typer(name_cmds.app, name="name", help="Naming-template lint and rename.")
app.add_typer(rule_cmds.app, name="rule", help="Edit rule field members (add/remove, idempotent).")
app.add_typer(set_cmds.app, name="set", help="Create or update address/service/tag objects.")
app.add_typer(
    audit_cmds.app,
    name="audit",
    help="Audit address objects for overlapping or contained CIDR ranges.",
)
app.add_typer(profile_cmds.app, name="profile", help="Manage live connection profiles.")
app.add_typer(version_cmds.app, name="version", help="Show version; check PyPI for updates.")
app.command(
    "init",
    help="Interactively bootstrap a live profile (fetches an API key from a username/password).",
)(auth_cmds.init)
app.command(
    "login",
    help="Verify a profile's API key — and rotate it with --user.",
)(auth_cmds.login)


def _emit_error(err: PscError) -> None:
    envelope = json.dumps(err.envelope(), ensure_ascii=False)
    if _ACTIVE is not None and _ACTIVE.output is OutputFormat.JSON:
        _ACTIVE.stdout.print(envelope, markup=False, highlight=False, soft_wrap=True)
    else:
        err_console = Console(stderr=True)
        err_console.print(f"[red]error[/red]: {err.message}", highlight=False)
        err_console.print(envelope, markup=False, highlight=False, soft_wrap=True)


def main() -> None:
    # Typer 0.26 vendors its own Click (`typer._click`), so the exceptions
    # `app(standalone_mode=False)` raises are NOT subclasses of the real
    # `click.*`. Catch both flavours, or a no-args/`--help`/bad-command run
    # escapes uncaught and prints a traceback instead of help (#31).
    try:
        app(standalone_mode=False)
    except PscError as err:
        _emit_error(err)
        raise SystemExit(err.exit_code) from None
    except (KeyboardInterrupt, click.exceptions.Abort, _typer_click_exc.Abort):
        Console(stderr=True).print("aborted")
        raise SystemExit(130) from None
    except (click.exceptions.Exit, _typer_click_exc.Exit) as err:
        raise SystemExit(err.exit_code) from None
    except (click.ClickException, _typer_click_exc.ClickException) as err:
        # `no_args_is_help` (and `--help`) already printed the help text; the
        # raise is just a stop signal, so exit 0 rather than rendering it as an
        # error. Genuine usage errors still print and exit non-zero.
        if err.__class__.__name__ == "NoArgsIsHelpError":
            raise SystemExit(0) from None
        err.show()
        raise SystemExit(err.exit_code) from None


if __name__ == "__main__":
    main()
