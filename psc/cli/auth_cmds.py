"""`psc init` / `psc login` — bootstrap a profile and verify/rotate its key.

These are the friendly front door to live access. `init` writes the first
profile (fetching an API key from a username/password, or accepting one you
paste). `login` runs a pre-flight probe against a stored profile and, when given
`--user`, rotates the key. Both keep the password in memory only; the key lands
in the `0600` config.

Password resolution order: `$PSC_PASSWORD`, then a hidden interactive prompt.
On a non-TTY with no env var we refuse rather than hang.
"""

from __future__ import annotations

import os
import sys

import typer

from psc.cli.runtime import Runtime
from psc.config.loader import save_config
from psc.config.models import Profile
from psc.core.source import LiveSource, SystemInfo
from psc.output.errors import ErrorType, PscError
from psc.output.format import render

_PASSWORD_ENV = "PSC_PASSWORD"


def _interactive() -> bool:
    return sys.stdin.isatty()


def _resolve_password(username: str) -> str:
    pw = os.environ.get(_PASSWORD_ENV)
    if pw:
        return pw
    if not _interactive():
        raise PscError(
            f"no password available: set ${_PASSWORD_ENV} or run interactively",
            ErrorType.CONFIG,
        )
    return str(typer.prompt(f"Password for {username}", hide_input=True))


def _announce_verified(rt: Runtime, info: SystemInfo) -> None:
    rt.stderr.print(
        f"[green]verified[/green] {info.hostname}: PAN-OS {info.version} "
        f"({info.model}, serial {info.serial})"
    )


def init(
    ctx: typer.Context,
    name: str = typer.Option("default", "--name", help="Profile name."),
    hostname: str | None = typer.Option(None, "--host", help="Panorama hostname or IP."),
    port: int = typer.Option(443, "--port"),
    device_group: str | None = typer.Option(
        None, "--device-group", "-d", help="Default device-group scope for this profile."
    ),
    username: str | None = typer.Option(
        None, "--user", help="Username to generate an API key for (prompts if omitted)."
    ),
    api_key: str | None = typer.Option(
        None, "--api-key", help="Use an existing key instead of generating one."
    ),
    no_verify: bool = typer.Option(False, "--no-verify", help="Skip the live pre-flight probe."),
    set_default: bool = typer.Option(
        True, "--default/--no-default", help="Make this the default profile."
    ),
) -> None:
    """Interactively bootstrap a live profile and write it to ~/.psc/config.yaml."""
    rt: Runtime = ctx.obj
    cfg = rt.config

    if hostname is None:
        if not _interactive():
            raise PscError("non-interactive init needs --host", ErrorType.CONFIG)
        hostname = typer.prompt("Panorama hostname")

    if api_key is None:
        if username is None:
            if not _interactive():
                raise PscError(
                    "non-interactive init needs --user (to generate a key) or --api-key",
                    ErrorType.CONFIG,
                )
            username = typer.prompt("Username")
        password = _resolve_password(username)
        rt.stderr.print(f"requesting API key from {hostname} …")
        api_key = LiveSource.fetch_api_key(hostname, username, password, port=port)
        rt.stderr.print("[green]received API key[/green]")

    if not no_verify:
        info = LiveSource(hostname, api_key, port=port).verify()
        _announce_verified(rt, info)

    # Replace any same-named profile, then append (mirrors `profile add`).
    cfg.profiles = [p for p in cfg.profiles if p.name != name]
    cfg.profiles.append(
        Profile(name=name, hostname=hostname, api_key=api_key, port=port, device_group=device_group)
    )
    if set_default or cfg.default_profile is None:
        cfg.default_profile = name
    path = save_config(cfg)
    rt.stderr.print(f"[green]saved[/green] profile '{name}' → {path}")


def login(
    ctx: typer.Context,
    name: str | None = typer.Option(
        None, "--name", help="Profile to log in (default: --profile, else the default profile)."
    ),
    username: str | None = typer.Option(
        None, "--user", help="Re-generate (rotate) the key for this user before verifying."
    ),
) -> None:
    """Verify a profile's API key — and rotate it when given --user."""
    rt: Runtime = ctx.obj
    cfg = rt.config

    target = name or rt.profile or cfg.default_profile
    if target is None:
        raise PscError("no profile to log in — run `psc init` first", ErrorType.CONFIG)
    prof = cfg.profile(target)
    if prof is None:
        raise PscError(f"no profile named '{target}' — run `psc init`", ErrorType.CONFIG)

    new_key = prof.api_key
    if username is not None:
        password = _resolve_password(username)
        rt.stderr.print(f"rotating API key for '{prof.name}' …")
        new_key = LiveSource.fetch_api_key(prof.hostname, username, password, port=prof.port)
    elif not prof.api_key:
        raise PscError(
            f"profile '{prof.name}' has no API key — run `psc login --user <name>`",
            ErrorType.CONFIG,
        )

    # Probe with the candidate key; only persist a rotation once it verifies.
    info = LiveSource(prof.hostname, new_key, port=prof.port, verify=prof.verify_ssl).verify()
    if username is not None:
        prof.api_key = new_key
        save_config(cfg)
        rt.stderr.print(f"[green]rotated + saved[/green] key for '{prof.name}'")
    _announce_verified(rt, info)

    rows = [
        {
            "profile": prof.name,
            "hostname": info.hostname,
            "version": info.version,
            "model": info.model,
            "serial": info.serial,
        }
    ]
    render(rt.stdout, rt.output, model=rows, rows=rows, table_title="login")
