from __future__ import annotations

from pathlib import Path

import pytest

from psc.core.models import Snapshot
from psc.core.parse import parse_config_file
from psc.core.refs import ReferenceGraph

FIXTURE = Path(__file__).parent / "fixtures" / "panorama-config.xml"
ALL_RB_FIXTURE = Path(__file__).parent / "fixtures" / "all-rulebases.xml"


@pytest.fixture
def fixture_path() -> Path:
    return FIXTURE


@pytest.fixture
def snapshot() -> Snapshot:
    return parse_config_file(FIXTURE)


@pytest.fixture
def graph(snapshot: Snapshot) -> ReferenceGraph:
    return ReferenceGraph.build(snapshot)


@pytest.fixture
def all_rb_path() -> Path:
    return ALL_RB_FIXTURE


@pytest.fixture
def all_rb_snapshot() -> Snapshot:
    return parse_config_file(ALL_RB_FIXTURE)


@pytest.fixture
def all_rb_graph(all_rb_snapshot: Snapshot) -> ReferenceGraph:
    return ReferenceGraph.build(all_rb_snapshot)
