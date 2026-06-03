"""Shared per-invocation state and helpers for every command.

`Runtime` carries the resolved global options (source, output format, apply
flag, scope, ...) so command bodies stay tiny. It also owns the two consoles —
stdout for data, stderr for human/debug chatter — which is what keeps
`-o json` pipes clean.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

import structlog
from rich.console import Console

from psc.config.models import Config
from psc.core.models import Location, Snapshot
from psc.core.source import LiveSource, OfflineSource
from psc.output.errors import ErrorType, PscError
from psc.output.format import OutputFormat


def configure_logging(debug: bool) -> None:
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            10 if debug else 30  # DEBUG vs WARNING
        ),
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


@dataclass
class Runtime:
    config: Config
    config_file: str | None
    profile: str | None
    debug: bool
    device_group: str | None
    strict: bool
    _output: OutputFormat | None
    stdout: Console = field(default_factory=Console)
    stderr: Console = field(default_factory=lambda: Console(stderr=True))
    _source: OfflineSource | LiveSource | None = None
    _snapshot: Snapshot | None = None

    @property
    def output(self) -> OutputFormat:
        if self._output is not None:
            return self._output
        # Agent-friendly default: non-TTY stdout => JSON, even if config says table.
        if not self.stdout.is_terminal:
            return OutputFormat.JSON
        return OutputFormat(self.config.defaults.output)

    def source(self) -> OfflineSource | LiveSource:
        if self._source is not None:
            return self._source
        if self.config_file:
            self._source = OfflineSource(self.config_file)
            return self._source
        prof = self.config.profile(self.profile)
        if prof is not None:
            self._source = LiveSource(
                prof.hostname, prof.api_key, port=prof.port, verify=prof.verify_ssl
            )
            return self._source
        raise PscError(
            "no config source: pass --config <export.xml> for offline, or "
            "configure a profile (`psc init`) and pass --profile <name>",
            ErrorType.CONFIG,
        )

    def snapshot(self) -> Snapshot:
        if self._snapshot is None:
            self._snapshot = self.source().snapshot()
        return self._snapshot

    def scope(self) -> Location | None:
        if not self.device_group:
            return None
        return Location.dg(self.device_group)
