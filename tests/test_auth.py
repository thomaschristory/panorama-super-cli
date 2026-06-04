"""Live keygen + verify: credentials → API key, and the pre-flight probe.

These never touch a real device — `panos.panorama.Panorama` is monkeypatched
with fakes that return a key / system-info or raise the SDK's typed errors, so
we assert the error → `ErrorType` mapping is right.
"""

from __future__ import annotations

import collections

import panos.panorama
import pytest
from panos.errors import (
    PanConnectionTimeout,
    PanDeviceError,
    PanInvalidCredentials,
    PanURLError,
)

from psc.core.source import LiveSource
from psc.output.errors import ErrorType, PscError

_SysInfo = collections.namedtuple("SystemInfo", ["version", "platform", "serial"])


class _FakePano:
    """Stand-in for pan-os-python's Panorama with scriptable behaviour."""

    def __init__(self, *args: object, raises: Exception | None = None, **kwargs: object) -> None:
        self._raises = raises

    def _retrieve_api_key(self) -> str:
        if self._raises is not None:
            raise self._raises
        return "LUFRPT1KEYABC123"

    def refresh_system_info(self) -> _SysInfo:
        if self._raises is not None:
            raise self._raises
        return _SysInfo(version="11.1.0", platform="Panorama", serial="0123456789")


def _patch_pano(monkeypatch: pytest.MonkeyPatch, raises: Exception | None = None) -> None:
    def factory(*args: object, **kwargs: object) -> _FakePano:
        return _FakePano(*args, raises=raises, **kwargs)

    monkeypatch.setattr(panos.panorama, "Panorama", factory)


def test_fetch_api_key_returns_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pano(monkeypatch)
    key = LiveSource.fetch_api_key("pano.example", "admin", "s3cret", port=443)
    assert key == "LUFRPT1KEYABC123"


def test_fetch_api_key_invalid_creds_is_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pano(monkeypatch, PanInvalidCredentials("Invalid credentials."))
    with pytest.raises(PscError) as exc:
        LiveSource.fetch_api_key("pano.example", "admin", "wrong")
    assert exc.value.error_type is ErrorType.AUTH


@pytest.mark.parametrize("err", [PanConnectionTimeout("t"), PanURLError("u")])
def test_fetch_api_key_unreachable_is_transport(
    monkeypatch: pytest.MonkeyPatch, err: Exception
) -> None:
    _patch_pano(monkeypatch, err)
    with pytest.raises(PscError) as exc:
        LiveSource.fetch_api_key("pano.example", "admin", "s3cret")
    assert exc.value.error_type is ErrorType.TRANSPORT


def test_fetch_api_key_generic_device_error_is_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pano(monkeypatch, PanDeviceError("boom"))
    with pytest.raises(PscError) as exc:
        LiveSource.fetch_api_key("pano.example", "admin", "s3cret")
    assert exc.value.error_type is ErrorType.TRANSPORT


def test_verify_returns_system_info(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pano(monkeypatch)
    info = LiveSource("pano.example", "KEY").verify()
    assert info.hostname == "pano.example"
    assert info.version == "11.1.0"
    assert info.model == "Panorama"
    assert info.serial == "0123456789"


def test_verify_invalid_creds_is_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pano(monkeypatch, PanInvalidCredentials("Invalid credentials."))
    with pytest.raises(PscError) as exc:
        LiveSource("pano.example", "STALEKEY").verify()
    assert exc.value.error_type is ErrorType.AUTH


def test_verify_unreachable_is_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pano(monkeypatch, PanConnectionTimeout("t"))
    with pytest.raises(PscError) as exc:
        LiveSource("pano.example", "KEY").verify()
    assert exc.value.error_type is ErrorType.TRANSPORT
