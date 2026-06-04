"""Nested device-group hierarchy: parsing, resolution, and shadow safety.

Hierarchy in the fixture (child → parent):

    shared
    └── EMEA
        └── EMEA-DC          (defines its own `h-shared`, shadowing shared)
            └── EMEA-DC-PROD  (grp-prod pulls members from every ancestor)

These lock the three #12 acceptance criteria: the parser captures parent/child,
resolution walks the chain shared-last (closest shadows), and merge/rename
shadow checks span ancestors *and* descendants.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from psc.core.changeset import ObjectKind
from psc.core.dedup import ObjectRef, plan_merge
from psc.core.models import SHARED, Location, Snapshot
from psc.core.naming import plan_rename
from psc.core.parse import parse_config_file
from psc.core.refs import ReferenceGraph
from psc.core.resolve import find_ip

FIXTURE = Path(__file__).parent / "fixtures" / "nested-device-groups.xml"

PROD = Location.dg("EMEA-DC-PROD")
DC = Location.dg("EMEA-DC")
EMEA = Location.dg("EMEA")


@pytest.fixture
def snap() -> Snapshot:
    return parse_config_file(FIXTURE)


@pytest.fixture
def graph(snap: Snapshot) -> ReferenceGraph:
    return ReferenceGraph.build(snap)


# -- parsing the hierarchy -------------------------------------------------


def test_parser_captures_parents(snap: Snapshot) -> None:
    assert set(snap.device_groups) == {"EMEA", "EMEA-DC", "EMEA-DC-PROD"}
    assert snap.device_group_parents == {"EMEA-DC": "EMEA", "EMEA-DC-PROD": "EMEA-DC"}


def test_objects_not_double_counted_from_readonly(snap: Snapshot) -> None:
    # The readonly hierarchy block must not be parsed as a second set of DGs/objects.
    h_dc = [a for a in snap.addresses if a.name == "h-dc"]
    assert len(h_dc) == 1
    assert h_dc[0].location == DC


def test_ancestors_chain_is_closest_first(snap: Snapshot) -> None:
    assert snap.ancestors(PROD) == [PROD, DC, EMEA, SHARED]
    assert snap.ancestors(EMEA) == [EMEA, SHARED]
    assert snap.ancestors(SHARED) == [SHARED]


def test_descendants(snap: Snapshot) -> None:
    assert snap.descendant_dgs("EMEA") == {"EMEA-DC", "EMEA-DC-PROD"}
    assert snap.descendant_dgs("EMEA-DC-PROD") == set()


# -- resolution walks the chain -------------------------------------------


def test_group_resolves_members_up_the_chain(graph: ReferenceGraph) -> None:
    # grp-prod (in PROD) pulls h-emea from a grandparent, h-dc from a parent.
    assert graph.resolve("address", "h-emea", PROD) is not None
    assert graph.resolve("address", "h-emea", PROD).location == EMEA  # type: ignore[union-attr]
    assert graph.resolve("address", "h-dc", PROD).location == DC  # type: ignore[union-attr]
    assert graph.resolve("address", "h-prod-local", PROD).location == PROD  # type: ignore[union-attr]


def test_closest_definition_shadows(graph: ReferenceGraph) -> None:
    # h-shared exists in shared AND EMEA-DC. From PROD the DC copy wins.
    t = graph.resolve("address", "h-shared", PROD)
    assert t is not None and t.location == DC
    # From EMEA (above the shadow) it falls through to shared.
    t2 = graph.resolve("address", "h-shared", EMEA)
    assert t2 is not None and t2.location == SHARED


def test_where_used_spans_levels(graph: ReferenceGraph) -> None:
    # h-emea@EMEA is referenced by grp-prod two levels down.
    refs = graph.where_used("address", "h-emea", EMEA)
    assert any(r.referrer_name == "grp-prod" for r in refs)


def test_no_dangling(graph: ReferenceGraph) -> None:
    assert graph.dangling() == []


def test_unused_accounts_for_descendant_use(graph: ReferenceGraph) -> None:
    unused = {(t.location.name, t.name) for t in graph.unused("address")}
    # Reached only from a descendant DG, but still used:
    assert ("EMEA", "h-emea") not in unused
    assert ("EMEA-DC", "h-dc") not in unused
    # The shared h-shared is shadowed by EMEA-DC's, so the reference never
    # reaches it → unused; the shadowing DC copy is used.
    assert ("shared", "h-shared") in unused
    assert ("EMEA-DC", "h-shared") not in unused
    # Never referenced anywhere.
    assert ("shared", "h-orphan") in unused


# -- find scope includes ancestors ----------------------------------------


def test_find_scope_includes_ancestors(snap: Snapshot) -> None:
    # An IP defined in a grandparent DG is visible when scoped to the leaf.
    res = find_ip(snap, "10.1.0.1", scope=PROD)
    assert res.exists
    assert {m.location for m in res.matches} == {"EMEA"}


def test_find_scope_excludes_descendants(snap: Snapshot) -> None:
    # A leaf-only object is NOT visible when scoped to an ancestor DG.
    res = find_ip(snap, "10.3.0.1", scope=EMEA)
    assert not res.exists


# -- shadow-aware safety guards -------------------------------------------


def test_rename_blocks_ancestor_shadow(snap: Snapshot, graph: ReferenceGraph) -> None:
    # Renaming a leaf object to a name an ANCESTOR defines would silently
    # re-point inherited references — refuse.
    cs = plan_rename(
        snap,
        graph,
        kind=ObjectKind.ADDRESS,
        location_name="EMEA-DC-PROD",
        old_name="h-prod-local",
        new_name="h-emea",
    )
    assert cs.is_blocked
    assert any("EMEA" in b for b in cs.blockers)


def test_rename_blocks_descendant_shadow(snap: Snapshot, graph: ReferenceGraph) -> None:
    # Renaming an ancestor object to a name a DESCENDANT defines is equally unsafe.
    cs = plan_rename(
        snap,
        graph,
        kind=ObjectKind.ADDRESS,
        location_name="EMEA",
        old_name="h-emea",
        new_name="h-prod-local",
    )
    assert cs.is_blocked
    assert any("EMEA-DC-PROD" in b for b in cs.blockers)


def test_merge_repoints_across_levels(snap: Snapshot, graph: ReferenceGraph) -> None:
    # Merge the leaf-local h-prod-local into the grandparent h-emea: the kept
    # object is visible at the referrer's scope through inheritance, so the
    # plan must succeed and repoint grp-prod.
    snap2 = snap.model_copy(deep=True)
    # make them mergeable (same value) by aligning h-prod-local to h-emea's value
    for a in snap2.addresses:
        if a.name == "h-prod-local":
            object.__setattr__(a, "value", "10.1.0.1/32")
    g2 = ReferenceGraph.build(snap2)
    cs = plan_merge(
        snap2,
        g2,
        keep=ObjectRef(name="h-emea", location="EMEA"),
        drop=ObjectRef(name="h-prod-local", location="EMEA-DC-PROD"),
    )
    assert not cs.is_blocked, cs.blockers
    edits = {(e.referrer_name, e.field): e.after for e in cs.reference_edits}
    assert "h-emea" in edits[("grp-prod", "static")]


def test_flat_config_still_parses_without_readonly() -> None:
    # Backward compatibility: a config with no readonly block → flat DGs.
    flat = parse_config_file(Path(__file__).parent / "fixtures" / "panorama-config.xml")
    assert flat.device_groups == ["DG-EDGE"]
    assert flat.device_group_parents == {}
    assert flat.ancestors(Location.dg("DG-EDGE")) == [Location.dg("DG-EDGE"), SHARED]
