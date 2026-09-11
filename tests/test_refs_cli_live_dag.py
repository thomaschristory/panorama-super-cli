"""The wire from `--live-dag` to the reference graph and to stderr (#183).

`tests/test_refs.py` drives the engine, and `tests/test_cli.py` shells out, so
neither can install a live source. These tests call the command bodies in
process, over a fake `LiveSource`. They pin the wire itself:

- the flag reaches `ReferenceGraph.build`, on `unused` and on `used`;
- `--live-dag-partial` reaches the source, and nothing else forces it;
- every live warning reaches stderr, with or without `--caveat`;
- the caveat reports the real unmatched count;
- `used` never calls an object unused on partial live data.
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import typer
from rich.console import Console

from psc.cli import refs_cmds
from psc.cli.runtime import Runtime
from psc.config.models import Config
from psc.core.livedag import (
    LiveDagMembership,
    RegisteredIps,
    UnreadableDevice,
    build_membership,
)
from psc.core.models import (
    Address,
    AddressGroup,
    AddressType,
    SecurityRule,
    Snapshot,
)
from psc.core.source import LiveSource
from psc.output.errors import ErrorType, PscError
from psc.output.format import OutputFormat


class _FakeLive(LiveSource):
    """A live source that answers from a hand-built membership map."""

    def __init__(self, membership: LiveDagMembership) -> None:
        super().__init__("pano.example", "LUFRPT1KEYABC123")
        self.membership = membership
        self.partial_calls: list[bool] = []

    def live_dag_membership(self, *, partial: bool = False) -> LiveDagMembership:
        self.partial_calls.append(partial)
        return self.membership


def _snapshot() -> Snapshot:
    """One address that only a registered IP puts in a rule-referenced DAG."""
    return Snapshot(
        addresses=[
            Address(name="h-vm", type=AddressType.IP_NETMASK, value="10.1.1.5/32"),
            Address(name="h-cold", type=AddressType.IP_NETMASK, value="10.3.3.3/32"),
        ],
        address_groups=[AddressGroup(name="dag-prod", dynamic_filter="'prod'")],
        security_rules=[SecurityRule(name="r", destination=["dag-prod"])],
    )


def _membership(
    *,
    values: dict[str, set[str]] | None = None,
    failed: list[str] | None = None,
    unreadable: list[str] | None = None,
    bad_row: bool = False,
) -> LiveDagMembership:
    rows = {
        v: frozenset(t) for v, t in ({"10.1.1.5": {"prod"}} if values is None else values).items()
    }
    warnings = ["`show object registered-ip all` returned an entry with no `ip` attribute"]
    return build_membership(
        {
            "001": RegisteredIps(
                by_value=rows,
                warnings=warnings if bad_row else [],
                unreadable_rows=1 if bad_row else 0,
            )
        },
        failed=failed or [],
        unreadable=[
            UnreadableDevice(label=s, reason=f"firewall {s} is not connected to Panorama.")
            for s in unreadable or []
        ],
    )


class _Case:
    """A command run: the runtime, the two streams, and the fake source."""

    def __init__(self, membership: LiveDagMembership | None, *, strict: bool = False) -> None:
        self.out = io.StringIO()
        self.err = io.StringIO()
        self.source = _FakeLive(membership) if membership is not None else None
        self.runtime = Runtime(
            config=Config(),
            config_file=None,
            profile="prod",
            debug=False,
            device_group=None,
            strict=strict,
            _output=OutputFormat.JSON,
            stdout=Console(file=self.out, width=200, no_color=True),
            stderr=Console(file=self.err, width=200, no_color=True),
            _source=self.source,
            _snapshot=_snapshot(),
        )

    @property
    def ctx(self) -> typer.Context:
        return cast(typer.Context, SimpleNamespace(obj=self.runtime))

    def stdout(self) -> str:
        return self.out.getvalue()

    def stderr(self) -> str:
        return self.err.getvalue()


def _unused(case: _Case, *, live_dag: bool, partial: bool = False, caveat: bool = True) -> None:
    refs_cmds.unused(
        case.ctx,
        kind="address",
        ignore_disabled=False,
        caveat=caveat,
        live_dag=live_dag,
        live_dag_partial=partial,
    )


def _used(case: _Case, name: str, *, live_dag: bool, partial: bool = False) -> None:
    refs_cmds.used(
        case.ctx,
        name=name,
        kind=None,
        location=None,
        live_dag=live_dag,
        live_dag_partial=partial,
    )


# --- the flag reaches the graph -------------------------------------------


def test_unused_lists_the_live_held_address_without_the_flag() -> None:
    case = _Case(_membership())
    _unused(case, live_dag=False)
    assert "h-vm" in case.stdout()


def test_unused_drops_the_live_held_address_with_the_flag() -> None:
    # CRITICAL (#183): this is the feature. Without the wire from the flag to
    # `ReferenceGraph.build`, psc reads every firewall and throws the answer
    # away, and h-vm goes on the delete list.
    case = _Case(_membership())
    _unused(case, live_dag=True)
    assert "h-vm" not in case.stdout()
    assert "h-cold" in case.stdout()  # the other address is really unused


def test_used_shows_the_live_derived_edge() -> None:
    case = _Case(_membership())
    _used(case, "h-vm", live_dag=True)
    assert "dynamic-registered" in case.stdout()
    assert "dag-prod" in case.stdout()


def test_used_shows_no_edge_without_the_flag() -> None:
    case = _Case(_membership())
    _used(case, "h-vm", live_dag=False)
    assert "dag-prod" not in case.stdout()


# --- the partial flag reaches the source ----------------------------------


def test_the_source_gets_the_partial_flag_the_caller_passed() -> None:
    for partial in (False, True):
        case = _Case(_membership())
        _unused(case, live_dag=True, partial=partial)
        assert case.source is not None
        assert case.source.partial_calls == [partial]


def test_the_flag_off_path_never_reads_the_device() -> None:
    # The offline contract: without the flag psc opens no op command at all, so
    # the output of a run without `--live-dag` cannot move.
    case = _Case(_membership())
    _unused(case, live_dag=False)
    _used(case, "h-vm", live_dag=False)
    assert case.source is not None
    assert case.source.partial_calls == []


def test_live_dag_partial_alone_is_a_config_error() -> None:
    case = _Case(_membership())
    with pytest.raises(PscError) as exc:
        _unused(case, live_dag=False, partial=True)
    assert exc.value.error_type is ErrorType.CONFIG


# --- every live warning reaches stderr ------------------------------------


def test_unused_prints_a_live_warning_on_stderr() -> None:
    case = _Case(_membership(bad_row=True))
    _unused(case, live_dag=True)
    assert "no `ip` attribute" in case.stderr()


def test_unused_names_the_failed_firewall_even_with_no_caveat() -> None:
    # HIGH (#183): `refs unused --no-caveat -o jsonl | psc delete` is the
    # documented pipeline. Partial coverage must not hide behind `--no-caveat`.
    case = _Case(_membership(failed=["002"]))
    _unused(case, live_dag=True, partial=True, caveat=False)
    assert "caveat" not in case.stderr()
    assert "002" in case.stderr()


def test_unused_names_the_failed_firewall_when_it_finds_nothing() -> None:
    # A run with no unused object prints no caveat. The warning channel still
    # states that one firewall did not answer.
    case = _Case(_membership(values={"10.1.1.5": {"prod"}, "10.3.3.3": {"prod"}}, failed=["002"]))
    _unused(case, live_dag=True, partial=True)
    assert case.stdout().strip() in ("[]", "")
    assert "002" in case.stderr()


def test_used_prints_the_live_warnings_and_the_partial_line() -> None:
    # HIGH (#183): `used` is the delete pre-flight, and it had no channel at all.
    case = _Case(_membership(failed=["002"], bad_row=True))
    _used(case, "h-vm", live_dag=True, partial=True)
    assert "no `ip` attribute" in case.stderr()
    assert "002" in case.stderr()


def test_used_prints_nothing_on_stderr_on_full_coverage() -> None:
    case = _Case(_membership())
    _used(case, "h-vm", live_dag=True)
    assert case.stderr() == ""


# --- the caveat -----------------------------------------------------------


def test_the_caveat_reports_the_real_unmatched_count() -> None:
    # One registered value matches h-vm, and one matches no address object.
    case = _Case(_membership(values={"10.1.1.5": {"prod"}, "10.2.2.2": {"prod"}}))
    _unused(case, live_dag=True)
    assert "carries 1 of the registered values" in case.stderr()


def test_the_caveat_changes_when_live_data_answers() -> None:
    offline = _Case(_membership())
    _unused(offline, live_dag=False)
    assert "externally registered IPs (config-tag DAG" in offline.stderr()
    live = _Case(_membership())
    _unused(live, live_dag=True)
    assert "IS scanned" in live.stderr()
    assert "1 firewall," in live.stderr()


# --- `used` never calls an object unused on partial data ------------------


def test_strict_used_refuses_to_call_an_object_unused_on_partial_data() -> None:
    # CRITICAL (#183): exit 5 means "psc found nothing". On partial live data
    # psc does not know that, so it must not answer 5.
    case = _Case(_membership(values={}, failed=["002"]), strict=True)
    with pytest.raises(PscError) as exc:
        _used(case, "h-vm", live_dag=True, partial=True)
    assert exc.value.error_type is not ErrorType.NOT_FOUND
    assert exc.value.error_type is ErrorType.TRANSPORT
    assert "002" in exc.value.message
    assert "002" in case.stderr()


def test_strict_used_still_answers_not_found_on_full_coverage() -> None:
    case = _Case(_membership(values={}), strict=True)
    with pytest.raises(PscError) as exc:
        _used(case, "h-vm", live_dag=True)
    assert exc.value.error_type is ErrorType.NOT_FOUND


def test_strict_unused_prints_the_warnings_before_it_refuses() -> None:
    live = _membership(values={"10.1.1.5": {"prod"}, "10.3.3.3": {"prod"}}, bad_row=True)
    case = _Case(live, strict=True)
    with pytest.raises(PscError) as exc:
        _unused(case, live_dag=True)
    assert exc.value.error_type is ErrorType.NOT_FOUND
    assert "no `ip` attribute" in case.stderr()


# --- the offline contract --------------------------------------------------


def test_an_offline_source_refuses_the_flag() -> None:
    case = _Case(None)
    case.runtime.config_file = str(Path(__file__).parent / "fixtures" / "panorama-config.xml")
    with pytest.raises(PscError) as exc:
        _unused(case, live_dag=True)
    assert exc.value.error_type is ErrorType.CONFIG


# --- a firewall psc cannot query at all -----------------------------------


def test_unused_names_a_firewall_psc_could_not_query() -> None:
    # CRITICAL (#183): Panorama named firewall 002, and psc could not read it.
    # The run must not claim full coverage, with or without the caveat.
    case = _Case(_membership(unreadable=["002"]))
    _unused(case, live_dag=True, partial=True, caveat=False)
    assert "002" in case.stderr()
    assert "coverage is partial" in case.stderr()


def test_the_caveat_degrades_for_a_firewall_psc_could_not_query() -> None:
    case = _Case(_membership(unreadable=["002"]))
    _unused(case, live_dag=True, partial=True)
    assert "IS scanned" not in case.stderr()
    assert "scanned in part" in case.stderr()
    assert "002" in case.stderr()


def test_strict_used_refuses_on_a_firewall_psc_could_not_query() -> None:
    case = _Case(_membership(values={}, unreadable=["002"]), strict=True)
    with pytest.raises(PscError) as exc:
        _used(case, "h-vm", live_dag=True, partial=True)
    assert exc.value.error_type is ErrorType.TRANSPORT
    assert "002" in exc.value.message
    assert "002" in case.stderr()


def test_strict_used_refuses_on_a_row_psc_could_not_read() -> None:
    # A row with no `ip` attribute holds an unknown subject. psc cannot rule
    # out that the row holds the registration of this object (#183).
    case = _Case(_membership(values={}, bad_row=True), strict=True)
    with pytest.raises(PscError) as exc:
        _used(case, "h-cold", live_dag=True)
    assert exc.value.error_type is ErrorType.TRANSPORT
    assert "could not read 1 registered row" in exc.value.message


def test_the_caveat_degrades_for_a_row_psc_could_not_read() -> None:
    case = _Case(_membership(bad_row=True))
    _unused(case, live_dag=True)
    assert "IS scanned" not in case.stderr()
    assert "could not read 1 registered row" in case.stderr()
