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

import ssl
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


class SystemInfo(BaseModel):
    """Result of the live pre-flight probe (`show system info`)."""

    hostname: str
    version: str
    model: str
    serial: str


def _ssl_context(verify: bool) -> ssl.SSLContext:
    """TLS context honouring a profile's `verify_ssl`.

    `pan-os-python` exposes no SSL knob and defaults to an *unverified* context,
    so we build our own and hand it to the underlying `xapi`. When `verify` is
    False (self-signed Panorama, common in labs) we explicitly disable checks —
    the call site has opted in.
    """
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


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

    @staticmethod
    def fetch_api_key(
        hostname: str, username: str, password: str, *, port: int = 443, verify: bool = True
    ) -> str:
        """Exchange a username/password for an API key via the PAN-OS keygen API.

        The credentials are never stored — only the returned key is. The keygen
        request travels over a TLS channel verified per `verify` (the SDK would
        otherwise never verify). Maps the SDK's typed failures onto our
        exit-code contract: bad credentials are an auth failure, anything that
        smells like a network/SSL problem is transport.
        """
        # Deferred so the offline path keeps no hard dependency on the SDK.
        from panos.base import PanDevice  # noqa: PLC0415
        from panos.errors import (  # noqa: PLC0415
            PanConnectionTimeout,
            PanDeviceError,
            PanInvalidCredentials,
            PanURLError,
        )
        from panos.panorama import Panorama  # noqa: PLC0415

        pano = Panorama(hostname, api_username=username, api_password=password, port=port)
        # Mirror the SDK's own keygen path (`_retrieve_api_key`) but hand it an
        # SSL context — the only injection point the SDK exposes for TLS. The
        # XapiWrapper keeps the password and key off the device object and
        # classifies failures into the typed errors below.
        xapi = PanDevice.XapiWrapper(
            pan_device=pano,
            api_username=username,
            api_password=password,
            hostname=hostname,
            port=port,
            timeout=pano.timeout,
            ssl_context=_ssl_context(verify),
        )
        try:
            xapi.keygen(retry_on_peer=False)
            key = str(xapi.api_key or "")
        except PanInvalidCredentials as exc:
            raise PscError(
                f"authentication failed for {username}@{hostname}: {exc}", ErrorType.AUTH
            ) from exc
        except (PanConnectionTimeout, PanURLError) as exc:
            raise PscError(f"cannot reach {hostname}: {exc}", ErrorType.TRANSPORT) from exc
        except PanDeviceError as exc:
            raise PscError(f"keygen failed on {hostname}: {exc}", ErrorType.TRANSPORT) from exc
        if not key:
            raise PscError(f"keygen on {hostname} returned an empty key", ErrorType.TRANSPORT)
        return key

    def verify(self) -> SystemInfo:
        """Pre-flight probe: confirm the key authenticates and the host answers.

        Runs `show system info`; an auth rejection maps to an auth failure, a
        connection/SSL problem to transport.
        """
        from panos.errors import (  # noqa: PLC0415
            PanConnectionTimeout,
            PanDeviceError,
            PanInvalidCredentials,
            PanURLError,
        )

        pano = self._device()
        try:
            info = pano.refresh_system_info()  # type: ignore[attr-defined]
        except PanInvalidCredentials as exc:
            raise PscError(f"API key rejected by {self.hostname}: {exc}", ErrorType.AUTH) from exc
        except (PanConnectionTimeout, PanURLError) as exc:
            raise PscError(f"cannot reach {self.hostname}: {exc}", ErrorType.TRANSPORT) from exc
        except PanDeviceError as exc:
            raise PscError(f"probe failed on {self.hostname}: {exc}", ErrorType.TRANSPORT) from exc
        return SystemInfo(
            hostname=self.hostname,
            version=str(info.version),
            model=str(info.platform),
            serial=str(info.serial),
        )

    def _device(self) -> object:
        from panos.panorama import Panorama  # noqa: PLC0415 — defer heavy SDK import to live use

        pano = Panorama(self.hostname, api_key=self._api_key, port=self._port)
        # The SDK never verifies TLS on its own; install our context before the
        # first request so reads/probes honour the profile's `verify_ssl`.
        pano.xapi.ssl_context = _ssl_context(self._verify)
        return pano

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
