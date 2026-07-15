"""`psc dedup promote` — CLI surface for the promote engine (issue #154).

Follows `test_cli_move.py`'s subprocess-driven idiom: `psc` is a real installed
console entry point, and every mutating command's safety contract (dry-run
default, blocked-plan exit 6, offline `--apply --out` round-trip) is exercised
end-to-end rather than through internal Typer/Click test runners.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from defusedxml.ElementTree import fromstring as xml_fromstring

# An empty `<shared>` element is required: `apply_xml` looks up the destination
# scope by name and raises if it isn't present in the source tree at all, even
# though promoting into it only ever *adds* an entry.
_XML = """<config><shared></shared><devices><entry name="localhost.localdomain"><device-group>
  <entry name="DG-A">
    <address><entry name="web"><ip-netmask>10.0.0.1/32</ip-netmask></entry></address>
  </entry>
  <entry name="DG-B">
    <address><entry name="web"><ip-netmask>10.0.0.1/32</ip-netmask></entry></address>
  </entry>
</device-group></entry></devices></config>"""

# Same value across DG-A and DG-B would make this one bucket in the usual case;
# here the two copies carry different *names* for the same value instead, which
# is what actually trips promote's "bucket names diverge" blocker. Using a
# genuine value mismatch would fail earlier — `find_duplicate_addresses` groups
# by value, so two different values are never bucketed together in the first
# place, and `--group` would 404 before `plan_promote` ever runs.
_DIVERGENT_NAMES_XML = """<config><shared></shared><devices><entry \
name="localhost.localdomain"><device-group>
  <entry name="DG-A">
    <address><entry name="web1"><ip-netmask>10.0.0.1/32</ip-netmask></entry></address>
  </entry>
  <entry name="DG-B">
    <address><entry name="web2"><ip-netmask>10.0.0.1/32</ip-netmask></entry></address>
  </entry>
</device-group></entry></devices></config>"""

# --keep's job is to repoint referrers, not just rename an unreferenced object,
# so each sibling DG gets its own local security rule naming its own copy —
# proof that `--keep h-web1` actually rewrites DG-B's rule onto the survivor
# rather than merely surviving because nothing pointed at the odd name.
_XML_DIVERGENT = """<config><shared></shared><devices><entry \
name="localhost.localdomain"><device-group>
  <entry name="DG-A">
    <address><entry name="h-web1"><ip-netmask>10.0.0.1/32</ip-netmask></entry></address>
    <pre-rulebase><security><rules>
      <entry name="allow-h-web1">
        <source><member>h-web1</member></source>
        <destination><member>any</member></destination>
        <service><member>any</member></service>
        <application><member>any</member></application>
        <action>allow</action>
      </entry>
    </rules></security></pre-rulebase>
  </entry>
  <entry name="DG-B">
    <address><entry name="web-primary"><ip-netmask>10.0.0.1/32</ip-netmask></entry></address>
    <pre-rulebase><security><rules>
      <entry name="allow-web-primary">
        <source><member>web-primary</member></source>
        <destination><member>any</member></destination>
        <service><member>any</member></service>
        <application><member>any</member></application>
        <action>allow</action>
      </entry>
    </rules></security></pre-rulebase>
  </entry>
</device-group></entry></devices></config>"""


def run(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PSC_CONFIG": "/nonexistent/psc-test-config.yaml"}
    return subprocess.run(
        [sys.executable, "-m", "psc", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _cfg(tmp_path: Path, xml: str = _XML) -> Path:
    p = tmp_path / "panorama.xml"
    p.write_text(xml)
    return p


def test_dry_run_prints_the_plan_and_writes_nothing(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    before = cfg.read_text()
    cp = run("-c", str(cfg), "dedup", "promote", "address", "--group", "10.0.0.1/32")
    assert cp.returncode == 0, cp.stderr
    assert "shared" in cp.stdout
    assert cfg.read_text() == before  # source export untouched


def test_apply_out_round_trips_offline(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    out = tmp_path / "out.xml"
    cp = run(
        "-c",
        str(cfg),
        "dedup",
        "promote",
        "address",
        "--group",
        "10.0.0.1/32",
        "--apply",
        "--out",
        str(out),
    )
    assert cp.returncode == 0, cp.stderr
    root = xml_fromstring(out.read_text())

    shared_names = {e.get("name") for e in root.findall("./shared/address/entry")}
    assert shared_names == {"web"}  # promoted to shared

    for dg in ("DG-A", "DG-B"):
        entry = next(
            e for e in root.findall("./devices/entry/device-group/entry") if e.get("name") == dg
        )
        assert entry.findall("./address/entry") == []  # both DG copies gone


def test_blocked_plan_exits_6_and_writes_nothing(tmp_path: Path) -> None:
    # DG-A/DG-B carry the same value under different names ("web1"/"web2") -> one
    # bucket by value, but promote refuses to pick a survivor name for you.
    cfg = _cfg(tmp_path, _DIVERGENT_NAMES_XML)
    out = tmp_path / "out.xml"
    cp = run(
        "-c",
        str(cfg),
        "dedup",
        "promote",
        "address",
        "--group",
        "10.0.0.1/32",
        "--apply",
        "--out",
        str(out),
    )
    assert cp.returncode == 6, cp.stdout + cp.stderr
    assert not out.exists()


def test_set_output_renders_create_and_delete_lines(tmp_path: Path) -> None:
    cp = run(
        "-c",
        str(_cfg(tmp_path)),
        "-o",
        "set",
        "dedup",
        "promote",
        "address",
        "--group",
        "10.0.0.1/32",
    )
    assert cp.returncode == 0, cp.stderr
    assert "set shared address web ip-netmask 10.0.0.1/32" in cp.stdout
    assert "delete device-group DG-A address web" in cp.stdout
    assert "delete device-group DG-B address web" in cp.stdout


# Two independent duplicate-address buckets across the same two DGs, so `--all`
# has more than one bucket to aggregate in a single plan.
_XML_TWO_BUCKETS = """<config><shared></shared><devices><entry \
name="localhost.localdomain"><device-group>
  <entry name="DG-A">
    <address>
      <entry name="web"><ip-netmask>10.0.0.1/32</ip-netmask></entry>
      <entry name="db"><ip-netmask>10.0.0.2/32</ip-netmask></entry>
    </address>
  </entry>
  <entry name="DG-B">
    <address>
      <entry name="web"><ip-netmask>10.0.0.1/32</ip-netmask></entry>
      <entry name="db"><ip-netmask>10.0.0.2/32</ip-netmask></entry>
    </address>
  </entry>
</device-group></entry></devices></config>"""


def test_all_promotes_every_bucket(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, _XML_TWO_BUCKETS)
    out = tmp_path / "out.xml"
    cp = run("-c", str(cfg), "dedup", "promote", "address", "--all", "--apply", "--out", str(out))
    assert cp.returncode == 0, cp.stderr
    root = xml_fromstring(out.read_text())

    shared_names = {e.get("name") for e in root.findall("./shared/address/entry")}
    assert shared_names == {"web", "db"}  # both buckets landed at shared

    for dg in ("DG-A", "DG-B"):
        entry = next(
            e for e in root.findall("./devices/entry/device-group/entry") if e.get("name") == dg
        )
        assert entry.findall("./address/entry") == []  # both DG copies of both gone


def test_group_and_all_together_is_a_usage_error(tmp_path: Path) -> None:
    cp = run(
        "-c",
        str(_cfg(tmp_path)),
        "dedup",
        "promote",
        "address",
        "--group",
        "10.0.0.1/32",
        "--all",
    )
    assert cp.returncode != 0


def test_neither_group_nor_all_is_a_usage_error(tmp_path: Path) -> None:
    cp = run("-c", str(_cfg(tmp_path)), "dedup", "promote", "address")
    assert cp.returncode != 0


def test_divergent_names_without_keep_exit_6(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, _XML_DIVERGENT)
    cp = run("-c", str(cfg), "dedup", "promote", "address", "--group", "10.0.0.1/32")
    assert cp.returncode == 6, cp.stdout + cp.stderr
    assert "names diverge" in cp.stdout + cp.stderr


def test_keep_unifies_them(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, _XML_DIVERGENT)
    out = tmp_path / "out.xml"
    cp = run(
        "-c",
        str(cfg),
        "dedup",
        "promote",
        "address",
        "--group",
        "10.0.0.1/32",
        "--keep",
        "h-web1",
        "--apply",
        "--out",
        str(out),
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr
    root = xml_fromstring(out.read_text())

    shared_names = {e.get("name") for e in root.findall("./shared/address/entry")}
    assert shared_names == {"h-web1"}  # unified onto the --keep name

    for dg in ("DG-A", "DG-B"):
        entry = next(
            e for e in root.findall("./devices/entry/device-group/entry") if e.get("name") == dg
        )
        assert entry.findall("./address/entry") == []  # both DG copies gone

    # DG-B's rule referenced the odd name; --keep must have repointed it.
    dgb = next(
        e for e in root.findall("./devices/entry/device-group/entry") if e.get("name") == "DG-B"
    )
    sources = {m.text for m in dgb.findall("./pre-rulebase/security/rules/entry/source/member")}
    assert sources == {"h-web1"}


def test_all_and_keep_together_is_a_usage_error(tmp_path: Path) -> None:
    cp = run(
        "-c",
        str(_cfg(tmp_path)),
        "dedup",
        "promote",
        "address",
        "--all",
        "--keep",
        "web",
    )
    assert cp.returncode != 0


# --- address-group buckets: --name selector + --cascade (#154 phase 3) ------

# Two sibling device-groups, each with its OWN copy of 'h-web1' and a 'web'
# group containing it. Neither the address nor the group has a shared-visible
# copy, so a bare promote blocks on the group's unresolved dependency
# (h-web1 isn't visible at shared) — exactly the case --cascade exists for.
_GROUP_XML = """<config><shared></shared><devices><entry \
name="localhost.localdomain"><device-group>
  <entry name="DG-A">
    <address><entry name="h-web1"><ip-netmask>10.0.0.1/32</ip-netmask></entry></address>
    <address-group>
      <entry name="web"><static><member>h-web1</member></static></entry>
    </address-group>
  </entry>
  <entry name="DG-B">
    <address><entry name="h-web1"><ip-netmask>10.0.0.1/32</ip-netmask></entry></address>
    <address-group>
      <entry name="web"><static><member>h-web1</member></static></entry>
    </address-group>
  </entry>
</device-group></entry></devices></config>"""


def _group_cfg(tmp_path: Path) -> Path:
    return _cfg(tmp_path, _GROUP_XML)


# Same two sibling 'web' groups, but the leaf they both reference already lives
# at shared — no unresolved dependency, so this bucket promotes cleanly WITHOUT
# --cascade. Used to prove the --group/--name kind-scoping guard actually fires
# (rather than the promote merely being blocked for an unrelated reason).
_GROUP_XML_LEAF_SHARED = """<config><shared>
  <address><entry name="h-web1"><ip-netmask>10.0.0.1/32</ip-netmask></entry></address>
</shared><devices><entry name="localhost.localdomain"><device-group>
  <entry name="DG-A">
    <address-group>
      <entry name="web"><static><member>h-web1</member></static></entry>
    </address-group>
  </entry>
  <entry name="DG-B">
    <address-group>
      <entry name="web"><static><member>h-web1</member></static></entry>
    </address-group>
  </entry>
</device-group></entry></devices></config>"""


def _group_cfg_leaf_shared(tmp_path: Path) -> Path:
    return _cfg(tmp_path, _GROUP_XML_LEAF_SHARED)


def test_group_promote_without_cascade_is_blocked(tmp_path: Path) -> None:
    cfg = _group_cfg(tmp_path)
    cp = run("-c", str(cfg), "dedup", "promote", "address-group", "--name", "web")
    assert cp.returncode == 6, cp.stdout + cp.stderr


def test_group_promote_with_cascade_pulls_the_leaves_up(tmp_path: Path) -> None:
    cfg = _group_cfg(tmp_path)
    out = tmp_path / "out.xml"
    cp = run(
        "-c",
        str(cfg),
        "dedup",
        "promote",
        "address-group",
        "--name",
        "web",
        "--cascade",
        "--apply",
        "--out",
        str(out),
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr
    root = xml_fromstring(out.read_text())

    shared_addr_names = {e.get("name") for e in root.findall("./shared/address/entry")}
    assert shared_addr_names == {"h-web1"}  # the leaf came up too
    shared_group_names = {e.get("name") for e in root.findall("./shared/address-group/entry")}
    assert shared_group_names == {"web"}

    for dg in ("DG-A", "DG-B"):
        entry = next(
            e for e in root.findall("./devices/entry/device-group/entry") if e.get("name") == dg
        )
        assert entry.findall("./address/entry") == []  # both DG copies of the leaf gone
        assert entry.findall("./address-group/entry") == []  # both DG copies of the group gone


def test_name_and_group_together_is_a_usage_error(tmp_path: Path) -> None:
    cp = run(
        "-c",
        str(_group_cfg(tmp_path)),
        "dedup",
        "promote",
        "address-group",
        "--name",
        "web",
        "--group",
        "web",
    )
    assert cp.returncode != 0


# --- kind-scoped selectors: --group/--name/--cascade match their documented kind ---


def test_name_on_a_value_keyed_kind_is_a_usage_error(tmp_path: Path) -> None:
    # --name is address-group-only (name-keyed); address buckets are value-keyed.
    cp = run("-c", str(_cfg(tmp_path)), "dedup", "promote", "address", "--name", "10.0.0.1/32")
    assert cp.returncode != 0


def test_group_on_a_name_keyed_kind_is_a_usage_error(tmp_path: Path) -> None:
    # --group is address/service-only (value-keyed); address-group buckets are name-keyed.
    # Uses the leaf-already-at-shared fixture, which promotes cleanly (no --cascade
    # needed): without the guard this would exit 0 via the --group/--name alias bug.
    cp = run(
        "-c",
        str(_group_cfg_leaf_shared(tmp_path)),
        "dedup",
        "promote",
        "address-group",
        "--group",
        "web",
    )
    assert cp.returncode != 0


def test_cascade_on_a_value_keyed_kind_is_a_usage_error(tmp_path: Path) -> None:
    # --cascade only makes sense for address-group (only groups have a member closure).
    cp = run(
        "-c",
        str(_cfg(tmp_path)),
        "dedup",
        "promote",
        "address",
        "--cascade",
        "--group",
        "10.0.0.1/32",
    )
    assert cp.returncode != 0


# --- tag dedup (#162) --------------------------------------------------------

# 'prod' redundantly defined in DG-A and DG-B with DIFFERENT colours: the bucket
# is name-keyed (colour is cosmetic), so it still consolidates, and the colour
# difference must surface as a drift warning rather than a blocker.
_TAG_XML = """<config><shared></shared><devices><entry name="localhost.localdomain"><device-group>
  <entry name="DG-A">
    <tag><entry name="prod"><color>color1</color></entry></tag>
  </entry>
  <entry name="DG-B">
    <tag><entry name="prod"><color>color5</color></entry></tag>
  </entry>
</device-group></entry></devices></config>"""

# A bare tag (no colour/comments) in two DGs: the promoted create carries no
# fields, so the `set` renderer must still emit `set shared tag prod`.
_BARE_TAG_XML = """<config><shared></shared><devices><entry \
name="localhost.localdomain"><device-group>
  <entry name="DG-A"><tag><entry name="prod"/></tag></entry>
  <entry name="DG-B"><tag><entry name="prod"/></tag></entry>
</device-group></entry></devices></config>"""


def test_dedup_tags_lists_redundant_tags(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, _TAG_XML)
    cp = run("-c", str(cfg), "-o", "json", "dedup", "tags")
    assert cp.returncode == 0, cp.stderr
    assert "prod" in cp.stdout
    assert "DG-A" in cp.stdout and "DG-B" in cp.stdout


def test_dedup_promote_tag_apply_promotes_to_shared(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, _TAG_XML)
    out = tmp_path / "out.xml"
    cp = run(
        "-c", str(cfg), "dedup", "promote", "tag", "--name", "prod", "--apply", "--out", str(out)
    )
    assert cp.returncode == 0, cp.stderr
    root = xml_fromstring(out.read_text())
    shared_tags = {e.get("name") for e in root.findall("./shared/tag/entry")}
    assert shared_tags == {"prod"}  # promoted to shared
    for dg in ("DG-A", "DG-B"):
        entry = next(
            e for e in root.findall("./devices/entry/device-group/entry") if e.get("name") == dg
        )
        assert entry.findall("./tag/entry") == []  # both DG copies gone


def test_dedup_promote_tag_warns_on_colour_drift(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, _TAG_XML)
    cp = run("-c", str(cfg), "dedup", "promote", "tag", "--name", "prod")
    assert cp.returncode == 0, cp.stderr
    assert "color" in (cp.stdout + cp.stderr)


def test_dedup_promote_tag_all_set_output_emits_bare_create(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, _BARE_TAG_XML)
    cp = run("-c", str(cfg), "-o", "set", "dedup", "promote", "tag", "--all")
    assert cp.returncode == 0, cp.stderr
    assert "set shared tag prod" in cp.stdout  # fieldless create still rendered
    assert "delete" in cp.stdout and "tag prod" in cp.stdout


def test_dedup_promote_tag_rejects_group_flag(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, _TAG_XML)
    cp = run("-c", str(cfg), "dedup", "promote", "tag", "--group", "prod")
    assert cp.returncode != 0
    assert "name-keyed" in (cp.stdout + cp.stderr)


def test_dedup_promote_tag_rejects_keep_flag(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, _TAG_XML)
    cp = run("-c", str(cfg), "dedup", "promote", "tag", "--name", "prod", "--keep", "prod")
    assert cp.returncode != 0
    assert "keep" in (cp.stdout + cp.stderr).lower()


def test_dedup_promote_tag_rejects_cascade_flag(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, _TAG_XML)
    cp = run("-c", str(cfg), "dedup", "promote", "tag", "--name", "prod", "--cascade")
    assert cp.returncode != 0
    assert "cascade" in (cp.stdout + cp.stderr).lower()
