"""The live side of `--live-dag`: read registered IPs over the XML API (#183).

No real device. The pan-os-python boundary is a fake that records each `op`
call and answers with hand-authored XML. These tests pin the safety contract:

- psc reads the registered IPs of each connected firewall, not of Panorama;
- psc refuses when no firewall answers, so it never reports empty live data as
  full coverage;
- one failed firewall stops the command, unless the caller accepts partial
  coverage.
"""

from __future__ import annotations

import panos.panorama
import pytest

from psc.core.livedag import CONNECTED_DEVICES_CMD, REGISTERED_IP_CMD
from psc.core.source import LiveSource
from psc.output.errors import EXIT_CODES, ErrorType, PscError

_DEVICES = """<response status="success"><result><devices>
  <entry name="001"><serial>001</serial><hostname>fw-a</hostname><connected>yes</connected></entry>
  <entry name="002"><serial>002</serial><hostname>fw-b</hostname><connected>yes</connected></entry>
</devices></result></response>"""

_REG_A = """<response status="success"><result>
  <entry ip="10.1.1.5"><tag><member>prod</member></tag></entry>
</result></response>"""

_REG_B = """<response status="success"><result>
  <entry ip="10.1.1.5"><tag><member>web</member></tag></entry>
  <entry ip="10.1.1.6"><tag><member>dev</member></tag></entry>
</result></response>"""


class _FakePano:
    """Stand-in for the Panorama device object on the op path."""

    def __init__(self, *, devices: str = _DEVICES, fail: frozenset[str] = frozenset()) -> None:
        self.xapi = _FakeXapi()
        self.calls: list[tuple[str, str | None]] = []
        self._devices = devices
        self._fail = fail

    def op(
        self,
        cmd: str,
        vsys: object = None,
        xml: bool = False,
        cmd_xml: bool = True,
        extra_qs: dict[str, str] | None = None,
        **kwargs: object,
    ) -> bytes:
        target = (extra_qs or {}).get("target")
        self.calls.append((cmd, target))
        if target in self._fail:
            raise RuntimeError(f"op failed on {target}")
        if cmd == CONNECTED_DEVICES_CMD:
            return self._devices.encode("utf-8")
        return {"001": _REG_A, "002": _REG_B}.get(str(target), "<response/>").encode("utf-8")


class _FakeXapi:
    def __init__(self) -> None:
        self.ssl_context: object = None


@pytest.fixture
def fake_pano(monkeypatch: pytest.MonkeyPatch) -> _FakePano:
    pano = _FakePano()
    monkeypatch.setattr(panos.panorama, "Panorama", lambda *a, **k: pano)
    return pano


def _live() -> LiveSource:
    return LiveSource("pano.example", "LUFRPT1KEYABC123", verify=False)


def test_op_returns_text_and_carries_the_target_serial(fake_pano: _FakePano) -> None:
    answer = _live().op(REGISTERED_IP_CMD, target="001")
    assert isinstance(answer, str)
    assert "10.1.1.5" in answer
    assert fake_pano.calls == [(REGISTERED_IP_CMD, "001")]


def test_op_wraps_a_device_failure_as_a_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pano = _FakePano(fail=frozenset({"001"}))
    monkeypatch.setattr(panos.panorama, "Panorama", lambda *a, **k: pano)
    with pytest.raises(PscError) as exc:
        _live().op(REGISTERED_IP_CMD, target="001")
    assert exc.value.error_type is ErrorType.TRANSPORT


def test_membership_reads_every_connected_firewall(fake_pano: _FakePano) -> None:
    m = _live().live_dag_membership()
    assert fake_pano.calls == [
        (CONNECTED_DEVICES_CMD, None),
        (REGISTERED_IP_CMD, "001"),
        (REGISTERED_IP_CMD, "002"),
    ]
    assert m.devices == ["001", "002"]
    assert m.indexed_values == 2
    assert m.is_partial is False
    assert m.by_key["ip-netmask:10.1.1.5/32"] == [frozenset({"prod"}), frozenset({"web"})]


def test_membership_refuses_when_no_firewall_is_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The worst failure mode is an empty answer that reads as "nothing is
    # registered". psc refuses instead, so it never softens the caveat.
    pano = _FakePano(devices='<response status="success"><result><devices/></result></response>')
    monkeypatch.setattr(panos.panorama, "Panorama", lambda *a, **k: pano)
    with pytest.raises(PscError) as exc:
        _live().live_dag_membership()
    assert exc.value.error_type is ErrorType.TRANSPORT


def test_the_no_firewall_exit_code_differs_from_the_empty_result_code() -> None:
    # `refs unused --strict` already exits 5 on an empty result. An agent must
    # be able to tell a clean estate from absent live data.
    assert EXIT_CODES[ErrorType.TRANSPORT] != EXIT_CODES[ErrorType.NOT_FOUND]


def test_one_failed_firewall_stops_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    pano = _FakePano(fail=frozenset({"002"}))
    monkeypatch.setattr(panos.panorama, "Panorama", lambda *a, **k: pano)
    with pytest.raises(PscError) as exc:
        _live().live_dag_membership()
    assert exc.value.error_type is ErrorType.TRANSPORT
    assert "002" in exc.value.message


def test_partial_coverage_keeps_the_firewalls_that_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pano = _FakePano(fail=frozenset({"002"}))
    monkeypatch.setattr(panos.panorama, "Panorama", lambda *a, **k: pano)
    m = _live().live_dag_membership(partial=True)
    assert m.devices == ["001"]
    assert m.failed_devices == ["002"]
    assert m.is_partial is True
    assert m.by_key["ip-netmask:10.1.1.5/32"] == [frozenset({"prod"})]


def test_partial_coverage_still_refuses_when_every_firewall_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pano = _FakePano(fail=frozenset({"001", "002"}))
    monkeypatch.setattr(panos.panorama, "Panorama", lambda *a, **k: pano)
    with pytest.raises(PscError) as exc:
        _live().live_dag_membership(partial=True)
    assert exc.value.error_type is ErrorType.TRANSPORT


_DEVICES_002_DOWN = """<response status="success"><result><devices>
  <entry name="001"><serial>001</serial><hostname>fw-a</hostname><connected>yes</connected></entry>
  <entry name="002"><serial>002</serial><hostname>fw-b</hostname><connected>no</connected></entry>
</devices></result></response>"""

_DEVICES_NO_SERIAL = """<response status="success"><result><devices>
  <entry name="001"><serial>001</serial><hostname>fw-a</hostname><connected>yes</connected></entry>
  <entry name="003"><hostname>fw-c</hostname><connected>yes</connected></entry>
</devices></result></response>"""


def test_a_disconnected_firewall_stops_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    # CRITICAL (#183): Panorama named firewall 002 and said that psc cannot
    # read it. That is the same coverage gap as a firewall that raises, so the
    # default run refuses in the same way.
    pano = _FakePano(devices=_DEVICES_002_DOWN)
    monkeypatch.setattr(panos.panorama, "Panorama", lambda *a, **k: pano)
    with pytest.raises(PscError) as exc:
        _live().live_dag_membership()
    assert exc.value.error_type is ErrorType.TRANSPORT
    assert "002" in exc.value.message
    # psc refuses before it queries any firewall.
    assert pano.calls == [(CONNECTED_DEVICES_CMD, None)]


def test_partial_coverage_names_a_disconnected_firewall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pano = _FakePano(devices=_DEVICES_002_DOWN)
    monkeypatch.setattr(panos.panorama, "Panorama", lambda *a, **k: pano)
    m = _live().live_dag_membership(partial=True)
    assert m.devices == ["001"]
    assert m.failed_devices == ["002"]
    assert m.is_partial is True
    assert any("002" in w for w in m.warnings)


def test_a_device_row_with_no_serial_stops_the_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pano = _FakePano(devices=_DEVICES_NO_SERIAL)
    monkeypatch.setattr(panos.panorama, "Panorama", lambda *a, **k: pano)
    with pytest.raises(PscError) as exc:
        _live().live_dag_membership()
    assert exc.value.error_type is ErrorType.TRANSPORT
    assert "fw-c" in exc.value.message


def test_partial_coverage_names_a_device_row_with_no_serial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pano = _FakePano(devices=_DEVICES_NO_SERIAL)
    monkeypatch.setattr(panos.panorama, "Panorama", lambda *a, **k: pano)
    m = _live().live_dag_membership(partial=True)
    assert m.failed_devices == ["fw-c"]
    assert m.is_partial is True
    assert any("fw-c" in w for w in m.warnings)


def test_an_answer_psc_cannot_read_is_a_failed_query(monkeypatch: pytest.MonkeyPatch) -> None:
    # The exit code an operator sees is 7, not 3. The command is correct, and
    # the answer of the device is what psc cannot read (#183).
    class _BadShape(_FakePano):
        def op(self, cmd: str, *a: object, **k: object) -> bytes:  # type: ignore[override]
            target = (k.get("extra_qs") or {}).get("target")  # type: ignore[union-attr]
            self.calls.append((cmd, target))
            if cmd == CONNECTED_DEVICES_CMD:
                return self._devices.encode("utf-8")
            return b'<response status="success"><result><count>1200</count></result></response>'

    pano = _BadShape()
    monkeypatch.setattr(panos.panorama, "Panorama", lambda *a, **k: pano)
    with pytest.raises(PscError) as exc:
        _live().live_dag_membership()
    assert exc.value.error_type is ErrorType.TRANSPORT
    assert EXIT_CODES[exc.value.error_type] == 7
