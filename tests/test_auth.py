"""Live keygen + verify: credentials → API key, and the pre-flight probe.

These never touch a real device — the pan-os-python SDK boundary is
monkeypatched with fakes that return a key / system-info or raise the SDK's
typed errors, so we assert the error → `ErrorType` mapping is right and that a
TLS context is always installed.
"""

from __future__ import annotations

import collections
import functools
import ssl
import types

import panos.base
import panos.panorama
import pytest
from panos.errors import (
    PanConnectionTimeout,
    PanDeviceError,
    PanInvalidCredentials,
    PanURLError,
)

from psc.core.source import InsecureTLSWarning, LiveSource
from psc.output.errors import ErrorType, PscError

_SysInfo = collections.namedtuple("SystemInfo", ["version", "platform", "serial"])


class _FakePano:
    """Stand-in for pan-os-python's Panorama (the device object)."""

    def __init__(self, *args: object, raises: Exception | None = None, **kwargs: object) -> None:
        self._raises = raises
        self.timeout = 1200
        # `_device()` sets `pano.xapi.ssl_context`; give it a writable target.
        self.xapi = types.SimpleNamespace(ssl_context=None)

    def refresh_system_info(self) -> _SysInfo:
        if self._raises is not None:
            raise self._raises
        return _SysInfo(version="11.1.0", platform="Panorama", serial="0123456789")


class _FakeXapi:
    """Stand-in for the credentialed XapiWrapper used during keygen."""

    captured_ssl: ssl.SSLContext | None = None

    def __init__(self, *args: object, raises: Exception | None = None, **kwargs: object) -> None:
        self._raises = raises
        type(self).captured_ssl = kwargs.get("ssl_context")  # type: ignore[assignment]
        self.api_key = ""

    def keygen(self, *args: object, **kwargs: object) -> None:
        if self._raises is not None:
            raise self._raises
        self.api_key = "LUFRPT1KEYABC123"


def _patch_keygen(monkeypatch: pytest.MonkeyPatch, raises: Exception | None = None) -> None:
    monkeypatch.setattr(panos.panorama, "Panorama", _FakePano)
    monkeypatch.setattr(
        panos.base.PanDevice, "XapiWrapper", functools.partial(_FakeXapi, raises=raises)
    )


def _patch_probe(monkeypatch: pytest.MonkeyPatch, raises: Exception | None = None) -> None:
    monkeypatch.setattr(panos.panorama, "Panorama", functools.partial(_FakePano, raises=raises))


def test_fetch_api_key_returns_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_keygen(monkeypatch)
    key = LiveSource.fetch_api_key("pano.example", "admin", "s3cret", port=443)
    assert key == "LUFRPT1KEYABC123"


def test_fetch_api_key_installs_tls_context(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_keygen(monkeypatch)
    LiveSource.fetch_api_key("pano.example", "admin", "s3cret", verify=True)
    ctx = _FakeXapi.captured_ssl
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED  # verification really enforced


def test_fetch_api_key_insecure_disables_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_keygen(monkeypatch)
    LiveSource.fetch_api_key("pano.example", "admin", "s3cret", verify=False)
    ctx = _FakeXapi.captured_ssl
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_NONE


def test_fetch_api_key_invalid_creds_is_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_keygen(monkeypatch, PanInvalidCredentials("Invalid credentials."))
    with pytest.raises(PscError) as exc:
        LiveSource.fetch_api_key("pano.example", "admin", "wrong")
    assert exc.value.error_type is ErrorType.AUTH


@pytest.mark.parametrize("err", [PanConnectionTimeout("t"), PanURLError("u")])
def test_fetch_api_key_unreachable_is_transport(
    monkeypatch: pytest.MonkeyPatch, err: Exception
) -> None:
    _patch_keygen(monkeypatch, err)
    with pytest.raises(PscError) as exc:
        LiveSource.fetch_api_key("pano.example", "admin", "s3cret")
    assert exc.value.error_type is ErrorType.TRANSPORT


def test_fetch_api_key_generic_device_error_is_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_keygen(monkeypatch, PanDeviceError("boom"))
    with pytest.raises(PscError) as exc:
        LiveSource.fetch_api_key("pano.example", "admin", "s3cret")
    assert exc.value.error_type is ErrorType.TRANSPORT


def test_verify_returns_system_info(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_probe(monkeypatch)
    info = LiveSource("pano.example", "KEY").verify()
    assert info.hostname == "pano.example"
    assert info.version == "11.1.0"
    assert info.model == "Panorama"
    assert info.serial == "0123456789"


@pytest.mark.parametrize(("verify", "mode"), [(True, ssl.CERT_REQUIRED), (False, ssl.CERT_NONE)])
def test_device_installs_tls_context(
    monkeypatch: pytest.MonkeyPatch, verify: bool, mode: ssl.VerifyMode
) -> None:
    # Locks the read/probe path: _device() must hand the SDK a context whose
    # verify_mode matches the profile, not the SDK's unverified default.
    _patch_probe(monkeypatch)
    pano = LiveSource("pano.example", "KEY", verify=verify)._device()
    assert pano.xapi.ssl_context.verify_mode == mode  # type: ignore[attr-defined]


def test_verify_invalid_creds_is_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_probe(monkeypatch, PanInvalidCredentials("Invalid credentials."))
    with pytest.raises(PscError) as exc:
        LiveSource("pano.example", "STALEKEY").verify()
    assert exc.value.error_type is ErrorType.AUTH


def test_verify_unreachable_is_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_probe(monkeypatch, PanConnectionTimeout("t"))
    with pytest.raises(PscError) as exc:
        LiveSource("pano.example", "KEY").verify()
    assert exc.value.error_type is ErrorType.TRANSPORT


def test_fetch_api_key_insecure_emits_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    # A password-bearing keygen over an unverified channel is MITM-able: warn loudly.
    _patch_keygen(monkeypatch)
    with pytest.warns(InsecureTLSWarning, match="credentials"):
        LiveSource.fetch_api_key("pano.example", "admin", "s3cret", verify=False)


def test_fetch_api_key_secure_no_warning(
    monkeypatch: pytest.MonkeyPatch, recwarn: pytest.WarningsRecorder
) -> None:
    _patch_keygen(monkeypatch)
    LiveSource.fetch_api_key("pano.example", "admin", "s3cret", verify=True)
    assert not [w for w in recwarn.list if issubclass(w.category, InsecureTLSWarning)]


def test_device_insecure_emits_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_probe(monkeypatch)
    with pytest.warns(InsecureTLSWarning):
        LiveSource("pano.example", "KEY", verify=False)._device()


def test_device_secure_no_warning(
    monkeypatch: pytest.MonkeyPatch, recwarn: pytest.WarningsRecorder
) -> None:
    _patch_probe(monkeypatch)
    LiveSource("pano.example", "KEY", verify=True)._device()
    assert not [w for w in recwarn.list if issubclass(w.category, InsecureTLSWarning)]
