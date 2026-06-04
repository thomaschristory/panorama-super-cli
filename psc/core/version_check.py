"""Check PyPI for a newer release of `panorama-super-cli` (issue #33).

Framework-free so a future web UI can reuse it: the engine returns an
`UpdateInfo` model and raises `PscError` on a transport problem; the CLI does
the formatting. The HTTP fetch is isolated behind `_fetch_latest` so tests can
monkeypatch it without touching the network.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from packaging.version import InvalidVersion, Version
from pydantic import BaseModel

from psc import __version__
from psc.output.errors import ErrorType, PscError

# The PyPI *distribution* name (see `pyproject.toml [project].name`), not the
# `psc` import package.
PYPI_JSON_URL = "https://pypi.org/pypi/panorama-super-cli/json"


class UpdateInfo(BaseModel):
    """The result of an update check — installed vs. latest published release."""

    installed: str
    latest: str
    update_available: bool


def _fetch_latest(url: str, timeout: float) -> str:
    """Return the latest published version string from a PyPI JSON endpoint."""
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": f"psc/{__version__}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    return str(data["info"]["version"])


def check_for_update(*, timeout: float = 5.0, url: str = PYPI_JSON_URL) -> UpdateInfo:
    """Compare the installed version against the latest on PyPI.

    Raises `PscError(TRANSPORT)` when PyPI is unreachable or the response is
    malformed, so a flaky network is a clean typed failure rather than a stack
    trace. Version comparison is PEP 440-aware; an unparseable remote version
    falls back to a plain string inequality so we never crash on odd data.
    """
    try:
        latest = _fetch_latest(url, timeout)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PscError(
            f"could not reach PyPI to check for updates: {exc}", ErrorType.TRANSPORT
        ) from exc
    except (KeyError, ValueError) as exc:
        raise PscError(f"unexpected PyPI response: {exc}", ErrorType.TRANSPORT) from exc

    try:
        update_available = Version(latest) > Version(__version__)
    except InvalidVersion:
        update_available = latest != __version__
    return UpdateInfo(installed=__version__, latest=latest, update_available=update_available)
