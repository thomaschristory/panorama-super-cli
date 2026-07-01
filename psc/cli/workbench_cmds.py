"""`psc workbench` (alias `psc w`) — launch the interactive TUI."""

from __future__ import annotations

import typer

from psc.cli.runtime import Runtime
from psc.core.source import LiveSource, OfflineSource
from psc.output.errors import ErrorType, PscError
from psc.tui.app import WorkbenchApp
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode


def build_session(
    *,
    config_file: str | None,
    profile: str | None,
    output_mode: OutputMode,
) -> WorkbenchSession:
    """Construct a session from an offline config or a live profile.

    Mirrors Runtime.source(): --config wins; else the named/default profile.
    """
    from psc.config.loader import config_path, load_config  # noqa: PLC0415

    source: OfflineSource | LiveSource
    if config_file:
        source = OfflineSource(config_file)
    else:
        cfg = load_config(config_path())
        prof = cfg.profile(profile)
        if prof is None:
            raise PscError(
                "no source: pass --config <export.xml> or configure a profile",
                ErrorType.CONFIG,
            )
        source = LiveSource(prof.hostname, prof.api_key, port=prof.port, verify=prof.verify_ssl)
    return WorkbenchSession(source=source, output_mode=output_mode)


def workbench(
    ctx: typer.Context,
    apply_out: str | None = typer.Option(
        None, "--apply-out", help="File to write when output mode is offline-apply."
    ),
) -> None:
    """Launch the interactive workbench TUI."""
    rt: Runtime = ctx.obj
    session = build_session(
        config_file=rt.config_file, profile=rt.profile, output_mode=OutputMode.SET
    )
    session.apply_out_path = apply_out
    WorkbenchApp(session).run()
