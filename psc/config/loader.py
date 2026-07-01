"""Load/save `~/.psc/config.yaml` with round-trip YAML."""

from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path

from platformdirs import user_config_dir
from ruamel.yaml import YAML

from psc.config.models import Config
from psc.output.errors import ErrorType, PscError

_APP = "psc"


def config_path() -> Path:
    override = os.environ.get("PSC_CONFIG")
    if override:
        return Path(override)
    return Path(user_config_dir(_APP)) / "config.yaml"


def load_config(path: Path | None = None) -> Config:
    # Callers may pass an already-resolved path so the value they display (e.g.
    # `psc profile list`) is provably the same file we loaded (#48).
    path = path if path is not None else config_path()
    if not path.exists():
        return Config()
    yaml = YAML(typ="safe")
    try:
        data = yaml.load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise PscError(f"invalid config at {path}: {exc}", ErrorType.CONFIG) from exc
    try:
        return Config.model_validate(data)
    except Exception as exc:
        raise PscError(f"config schema error at {path}: {exc}", ErrorType.CONFIG) from exc


def save_config(config: Config) -> Path:
    path = config_path()
    # The config holds API keys, so the file must never exist world-readable —
    # not even for the create→chmod window. Create the parent dir private (0700)
    # and open the file 0600 *atomically* (O_CREAT with mode), writing the
    # secrets only through that already-restricted fd.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _tighten_config_dir(path.parent)
    buf = io.StringIO()
    # Inline YAML setup (not shared with psc.output) so config persistence never
    # depends on the rendering layer.
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.dump(config.model_dump(mode="json"), buf)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())
    # O_CREAT honours `mode` only on first create (and is umask-masked); a
    # pre-existing file keeps its old bits, so re-assert 0600 explicitly.
    _chmod_quietly(path, 0o600)
    return path


def _tighten_config_dir(directory: Path) -> None:
    """Ensure only the psc config dir is private (0700).

    `mkdir(mode=0o700)` is a no-op when the dir already exists and is subject to
    the umask on creation, so re-assert 0700 on *this* dir only — never touching
    unrelated parents, and never crashing on a platform without chmod semantics.
    """
    _chmod_quietly(directory, 0o700)


def _chmod_quietly(target: Path, mode: int) -> None:
    # chmod is a POSIX concept; on platforms where it is unsupported or the OS
    # refuses (e.g. some Windows filesystems) fall back gracefully rather than
    # crash — the O_CREAT mode already did the best-effort tightening.
    with contextlib.suppress(NotImplementedError, OSError):
        target.chmod(mode)
