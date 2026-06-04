"""Where a config comes from, and where writes go.

A `Source` unifies the offline and live paths behind one interface so every
engine and command is source-agnostic. The offline source reads (and rewrites)
an exported XML file; the live source fetches the running config from Panorama
over the XML API. Both produce the same `Snapshot`.

Writes are deliberately conservative:

- **Offline** `apply` never touches the input file — it writes the rewritten
  config to a separate path. That keeps the original export pristine and the
  operation reviewable.
- **Live** `apply` pushes the plan to Panorama's *candidate* config over the
  XML API and never commits — the operator owns the commit, so the result is a
  reviewable candidate, the device-side analog of the offline rewritten file.
"""

from __future__ import annotations

import ssl
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

from psc.core.apply_xml import apply_changeset
from psc.core.changeset import ChangeSet
from psc.core.models import Snapshot
from psc.core.parse import parse_config
from psc.core.setcmd import render_changeset
from psc.output.errors import ErrorType, PscError


class ConfigFormat(str, Enum):
    """Format of the artifact an offline `apply` writes to `--out`.

    `xml` rewrites the whole exported config (loadable with `load config`);
    `set` emits the equivalent PAN-OS `set` script (the creations/deletes/
    repoints that achieve the same change) — easier to read and to paste into a
    config session or `load config partial`.
    """

    XML = "xml"
    SET = "set"


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


def _write_artifact(out: Path, data: str) -> None:
    """Write an `--out` artifact, mapping filesystem errors onto the error
    contract. A bad `--out` (missing parent dir, a directory, unwritable) is an
    *expected* failure — surface it as `INPUT` rather than leaking a raw
    `OSError` traceback (which would also corrupt machine output)."""
    try:
        out.write_text(data, encoding="utf-8")
    except OSError as exc:
        raise PscError(f"cannot write artifact to {out}: {exc}", ErrorType.INPUT) from exc


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

    def write_out(
        self,
        cs: ChangeSet,
        *,
        out_path: str | Path | None,
        out_format: ConfigFormat = ConfigFormat.XML,
    ) -> ApplyResult:
        """Render `cs` to the `--out` artifact file (set script or rewritten XML).

        This is the *artifact* path: it only ever writes the separate `--out`
        file and never the source export, so it is safe in a dry-run. For the
        offline source the artifact *is* the whole point — `apply` is just this
        with an `applied=True` flag (there is no separate device to push to).
        """
        if out_path is None:
            raise PscError(
                "offline --out needs a PATH (the rewritten config is never "
                "written back over the source export)",
                ErrorType.CONFIG,
            )
        out = Path(out_path)
        if out.resolve() == self.path.resolve():
            raise PscError("--out must differ from the source config path", ErrorType.CONFIG)
        # The blocker gate is enforced here for *every* format — the XML path
        # relies on `apply_changeset`, but the set path renders the plan without
        # it, so refuse before writing rather than emit a `# BLOCKED` file.
        if cs.is_blocked:
            raise PscError(
                "refusing to write a blocked plan",
                ErrorType.CONFLICT,
                details={"blockers": cs.blockers},
            )
        # Both artifacts share the same safety guards above; only the bytes differ.
        if out_format is ConfigFormat.SET:
            script = render_changeset(cs)
            _write_artifact(out, "\n".join(script) + "\n")
            return ApplyResult(applied=False, ops=cs.op_count, out_path=str(out), set_script=script)
        new_xml = apply_changeset(self._xml, cs)
        _write_artifact(out, new_xml)
        return ApplyResult(applied=False, ops=cs.op_count, out_path=str(out))

    def apply(
        self,
        cs: ChangeSet,
        *,
        out_path: str | Path | None,
        out_format: ConfigFormat = ConfigFormat.XML,
    ) -> ApplyResult:
        # Offline has no live device: applying *is* producing the `--out`
        # artifact. Reuse the artifact path and only flip the `applied` flag.
        res = self.write_out(cs, out_path=out_path, out_format=out_format)
        return res.model_copy(update={"applied": True})


class LiveSource:
    """Fetch the running config from Panorama over the XML API, and push plans
    to its candidate config.

    Reads and writes are both supported; `apply` pushes the plan but never
    commits. The `pan-os-python` import is deferred so the offline path has no
    hard dependency on a reachable device at import time.
    """

    read_only = False

    def __init__(
        self, hostname: str, api_key: str, *, port: int = 443, verify: bool = True
    ) -> None:
        self.hostname = hostname
        self._api_key = api_key
        self._port = port
        self._verify = verify
        self._raw: str | None = None

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
        # Memoised for the lifetime of this source: one CLI invocation reads the
        # running config once and reuses it for the snapshot *and* any `--out`
        # XML artifact, so the artifact is rewritten from the exact config the
        # plan was computed against (no divergence, no second round-trip).
        if self._raw is not None:
            return self._raw
        try:
            pano = self._device()
            pano.xapi.show(xpath="/config")  # type: ignore[attr-defined]
            self._raw = str(pano.xapi.xml_result())  # type: ignore[attr-defined]
            return self._raw
        except PscError:
            raise
        except Exception as exc:
            raise PscError(
                f"failed to fetch config from {self.hostname}: {exc}", ErrorType.TRANSPORT
            ) from exc

    def snapshot(self) -> Snapshot:
        return parse_config(self.raw_xml())

    def write_out(
        self,
        cs: ChangeSet,
        *,
        out_path: str | Path | None,
        out_format: ConfigFormat = ConfigFormat.XML,
    ) -> ApplyResult:
        """Render `cs` to the `--out` artifact file from the live config.

        This never touches the device's candidate config — it only writes a
        local file the operator can review or paste. A `set` script is pure
        rendering of the plan; an `xml` artifact is the *running* config (read
        over the API) rewritten by the same offline applier. Honoured in a
        dry-run because emitting a file is not a device mutation.
        """
        if out_path is None:
            raise PscError("--out needs a PATH", ErrorType.CONFIG)
        out = Path(out_path)
        # Same blocker gate as every other write path: refuse before any bytes
        # hit disk rather than leave a misleading artifact.
        if cs.is_blocked:
            raise PscError(
                "refusing to write a blocked plan",
                ErrorType.CONFLICT,
                details={"blockers": cs.blockers},
            )
        if out_format is ConfigFormat.SET:
            script = render_changeset(cs)
            _write_artifact(out, "\n".join(script) + "\n")
            return ApplyResult(applied=False, ops=cs.op_count, out_path=str(out), set_script=script)
        new_xml = apply_changeset(self.raw_xml(), cs)
        _write_artifact(out, new_xml)
        return ApplyResult(applied=False, ops=cs.op_count, out_path=str(out))

    def apply(
        self,
        cs: ChangeSet,
        *,
        out_path: str | Path | None,
        out_format: ConfigFormat = ConfigFormat.XML,
    ) -> ApplyResult:
        """Push `cs` to Panorama's candidate config over the XML API.

        When `out_path` is given, the `--out` artifact is written *first* (so a
        reviewable file survives even if the device push later fails midway),
        then the plan is pushed to the candidate.

        The plan is lowered to xpath set/edit/delete/rename ops and replayed in
        order (repoint before delete). The `blockers` gate and name-addressing
        check run *before* any device contact, so a refused plan never writes.
        We deliberately **never commit** — the operator owns that step; this
        leaves a reviewable candidate exactly as the offline path leaves a file.

        A failure mid-replay surfaces as a transport error; because nothing is
        committed, the partial candidate stays on the device for the operator to
        inspect or revert (`load config` / `revert config`).
        """

        from psc.core.apply_live import XapiOp, plan_xapi_ops  # noqa: PLC0415

        # Raises CONFLICT (blocked) or INPUT (unaddressable name / unsupported
        # live update) before we touch the device.
        ops = plan_xapi_ops(cs)

        # Write the reviewable artifact first so it survives a failed push.
        artifact = (
            self.write_out(cs, out_path=out_path, out_format=out_format)
            if out_path is not None
            else None
        )

        from panos.errors import PanConnectionTimeout, PanURLError  # noqa: PLC0415

        pano = self._device()
        xapi = pano.xapi  # type: ignore[attr-defined]
        sent = 0
        op: XapiOp | None = None
        try:
            for op in ops:
                if op.action == "set":
                    xapi.set(xpath=op.xpath, element=op.element)
                elif op.action == "edit":
                    xapi.edit(xpath=op.xpath, element=op.element)
                elif op.action == "delete":
                    xapi.delete(xpath=op.xpath)
                else:  # "rename"
                    xapi.rename(xpath=op.xpath, newname=op.newname)
                sent += 1
        except (PanConnectionTimeout, PanURLError) as exc:
            raise PscError(
                f"cannot reach {self.hostname} after {sent}/{len(ops)} op(s) "
                f"(uncommitted candidate left for review): {exc}",
                ErrorType.TRANSPORT,
            ) from exc
        except Exception as exc:
            # Any other XML-API failure must not escape the PscError contract or
            # leak a traceback into machine output. Notably `pan.xapi.PanXapiError`
            # (bad xpath, HTTP error) is NOT a `panos.errors.PanDeviceError`, so a
            # typed catch would miss it. Report *where* we stopped: ops before
            # `sent` are on the candidate, the rest are not — the operator needs
            # that to reason about the partial state before they commit/revert.
            where = f" at op {sent + 1}/{len(ops)} ({op.action} {op.xpath})" if op else ""
            raise PscError(
                f"apply failed on {self.hostname}{where}; {sent} op(s) already on "
                f"the uncommitted candidate — inspect or revert on the device: {exc}",
                ErrorType.TRANSPORT,
                details={"sent": sent, "planned": len(ops)},
            ) from exc
        return ApplyResult(
            applied=True,
            ops=len(ops),
            out_path=artifact.out_path if artifact else None,
            set_script=artifact.set_script if artifact else [],
        )
