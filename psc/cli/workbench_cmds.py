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
        source = prof.to_live_source()
    return WorkbenchSession(source=source, output_mode=output_mode)


def workbench(
    ctx: typer.Context,
    output_mode: OutputMode = typer.Option(
        OutputMode.SET,
        "--output-mode",
        help="How a staged batch applies: 'set' prints the PAN-OS script, "
        "'offline-apply' writes the compounded config to --apply-out, "
        "'live-apply' pushes to the live candidate (never commits).",
    ),
    apply_out: str | None = typer.Option(
        None, "--apply-out", help="File to write when --output-mode is offline-apply."
    ),
) -> None:
    """Launch the interactive workbench TUI."""
    rt: Runtime = ctx.obj
    # Passing --apply-out is an unambiguous request for offline-apply; honour it
    # so the flag is never a silent no-op under the default SET mode.
    if apply_out is not None and output_mode is OutputMode.SET:
        output_mode = OutputMode.OFFLINE_APPLY
    # Fail fast rather than letting the user stage a whole batch and only
    # discover at apply time that offline-apply has nowhere to write.
    if output_mode is OutputMode.OFFLINE_APPLY and apply_out is None:
        raise PscError(
            "--output-mode offline-apply requires --apply-out <file>",
            ErrorType.CONFIG,
        )
    session = build_session(config_file=rt.config_file, profile=rt.profile, output_mode=output_mode)
    session.apply_out_path = apply_out
    WorkbenchApp(session).run()
