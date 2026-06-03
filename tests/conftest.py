from __future__ import annotations

from pathlib import Path

import pytest

from psc.core.models import Snapshot
from psc.core.parse import parse_config_file
from psc.core.refs import ReferenceGraph

FIXTURE = Path(__file__).parent / "fixtures" / "panorama-config.xml"


@pytest.fixture
def fixture_path() -> Path:
    return FIXTURE


@pytest.fixture
def snapshot() -> Snapshot:
    return parse_config_file(FIXTURE)


@pytest.fixture
def graph(snapshot: Snapshot) -> ReferenceGraph:
    return ReferenceGraph.build(snapshot)
