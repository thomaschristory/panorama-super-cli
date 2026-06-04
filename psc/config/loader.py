"""Load/save `~/.psc/config.yaml` with round-trip YAML."""

from __future__ import annotations

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
    path.parent.mkdir(parents=True, exist_ok=True)
    yaml = YAML()
    yaml.default_flow_style = False
    buf = io.StringIO()
    yaml.dump(config.model_dump(mode="json"), buf)
    path.write_text(buf.getvalue(), encoding="utf-8")
    path.chmod(0o600)  # contains API keys
    return path
