"""Where a config comes from, and where writes go.

A `Source` unifies the offline and live paths behind one interface so every
engine and command is source-agnostic. The offline source reads (and rewrites)
an exported XML file; the live source fetches the running config from Panorama
over the XML API. Both produce the same `Snapshot`.

Writes are deliberately conservative:

- **Offline** `apply` never touches the input file — it writes the rewritten
  config to a separate path. That keeps the original export pristine and the
  operation reviewable.
- **Live** `apply` is not yet implemented (v0.2). Until then the actionable
  artifact for a live config is the rendered `set` script; `psc` refuses to
  half-write to a production device.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from psc.core.apply_xml import apply_changeset
from psc.core.changeset import ChangeSet
from psc.core.models import Snapshot
from psc.core.parse import parse_config
from psc.output.errors import ErrorType, PscError


class ApplyResult(BaseModel):
    applied: bool
    ops: int
    out_path: str | None = None
    set_script: list[str] = Field(default_factory=list)


class OfflineSource:
    """Read + rewrite an exported Panorama config XML on disk."""

    read_only = False

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise PscError(f"config file not found: {self.path}", ErrorType.INPUT)
        try:
            self._xml = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise PscError(f"cannot read {self.path}: {exc}", ErrorType.INPUT) from exc

    def raw_xml(self) -> str:
        return self._xml

    def snapshot(self) -> Snapshot:
        try:
            return parse_config(self._xml)
        except Exception as exc:
            raise PscError(f"failed to parse {self.path}: {exc}", ErrorType.INPUT) from exc

    def apply(self, cs: ChangeSet, *, out_path: str | Path | None) -> ApplyResult:
        if out_path is None:
            raise PscError(
                "offline apply needs --out PATH (the rewritten config is never "
                "written back over the source export)",
                ErrorType.CONFIG,
            )
        out = Path(out_path)
        if out.resolve() == self.path.resolve():
            raise PscError("--out must differ from the source config path", ErrorType.CONFIG)
        new_xml = apply_changeset(self._xml, cs)
        out.write_text(new_xml, encoding="utf-8")
        return ApplyResult(applied=True, ops=cs.op_count, out_path=str(out))


class LiveSource:
    """Fetch the running config from Panorama over the XML API.

    Reads are fully supported; writes raise until v0.2. The `pan-os-python`
    import is deferred so the offline path has no hard dependency on a
    reachable device at import time.
    """

    read_only = True

    def __init__(
        self, hostname: str, api_key: str, *, port: int = 443, verify: bool = True
    ) -> None:
        self.hostname = hostname
        self._api_key = api_key
        self._port = port
        self._verify = verify

    def _device(self) -> object:
        from panos.panorama import Panorama  # noqa: PLC0415 — defer heavy SDK import to live use

        return Panorama(self.hostname, api_key=self._api_key, port=self._port)

    def raw_xml(self) -> str:
        try:
            pano = self._device()
            pano.xapi.show(xpath="/config")  # type: ignore[attr-defined]
            return str(pano.xapi.xml_result())  # type: ignore[attr-defined]
        except PscError:
            raise
        except Exception as exc:
            raise PscError(
                f"failed to fetch config from {self.hostname}: {exc}", ErrorType.TRANSPORT
            ) from exc

    def snapshot(self) -> Snapshot:
        return parse_config(self.raw_xml())

    def apply(self, cs: ChangeSet, *, out_path: str | Path | None) -> ApplyResult:
        raise PscError(
            "live apply lands in v0.2 — for now apply the rendered `set` script "
            "(`-o set`) or plan offline with --config and --apply --out",
            ErrorType.CONFIG,
        )
