from __future__ import annotations

import json

from psc.core.models import SHARED, Location
from psc.core.refs import Reference, Target
from psc.output.format import to_jsonable


def test_location_serializes_as_name() -> None:
    assert to_jsonable(SHARED) == "shared"
    assert to_jsonable(Location.dg("DG1")) == "DG1"


def test_location_roundtrips_from_string() -> None:
    loc = Location.model_validate("DG1")
    assert loc.device_group == "DG1"
    assert Location.model_validate("shared").is_shared


def test_dataclass_is_jsonable() -> None:
    ref = Reference(
        target_name="x",
        namespace="address",
        referrer_kind="security-rule",
        referrer_name="r",
        referrer_location=SHARED,
        field="source",
        resolved=Target("address", "x", SHARED),
    )
    payload = to_jsonable(ref)
    # round-trips through json without error
    json.dumps(payload)
    assert payload["resolved"]["location"] == "shared"
    assert payload["referrer_location"] == "shared"
