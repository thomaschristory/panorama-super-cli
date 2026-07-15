from __future__ import annotations

import pytest

from psc.core.source import OfflineSource
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode, SelectionItem

WORKBENCH_XML = """<?xml version="1.0"?>
<config>
  <shared>
    <address>
      <entry name="web-srv-01"><ip-netmask>10.0.5.10/32</ip-netmask></entry>
      <entry name="web-srv-02"><ip-netmask>10.0.5.10/32</ip-netmask></entry>
      <entry name="db-gw"><ip-netmask>10.0.9.1/32</ip-netmask></entry>
    </address>
    <service>
      <entry name="tcp-8443"><protocol><tcp><port>8443</port></tcp></protocol></entry>
    </service>
  </shared>
  <devices>
    <entry name="localhost.localdomain">
      <device-group/>
    </entry>
  </devices>
</config>
"""


@pytest.fixture
def workbench_xml(tmp_path):
    """Write the fixture config to a temp file, return its path (str)."""
    p = tmp_path / "config.xml"
    p.write_text(WORKBENCH_XML, encoding="utf-8")
    return str(p)


WORKBENCH_XML_TRIPLE = """<?xml version="1.0"?>
<config>
  <shared>
    <address>
      <entry name="web-srv-01"><ip-netmask>10.0.5.10/32</ip-netmask></entry>
      <entry name="web-srv-02"><ip-netmask>10.0.5.10/32</ip-netmask></entry>
      <entry name="web-srv-03"><ip-netmask>10.0.5.10/32</ip-netmask></entry>
      <entry name="db-gw"><ip-netmask>10.0.9.1/32</ip-netmask></entry>
    </address>
    <address-group>
      <entry name="web-pool">
        <static>
          <member>web-srv-01</member>
          <member>web-srv-02</member>
          <member>web-srv-03</member>
        </static>
      </entry>
    </address-group>
  </shared>
  <devices>
    <entry name="localhost.localdomain">
      <device-group/>
    </entry>
  </devices>
</config>
"""


@pytest.fixture
def workbench_xml_triple(tmp_path):
    """Three address objects sharing 10.0.5.10/32 (web-srv-01/02/03), all members
    of 'web-pool' — the 3+ duplicate collapse case for dedup (#85)."""
    p = tmp_path / "config_triple.xml"
    p.write_text(WORKBENCH_XML_TRIPLE, encoding="utf-8")
    return str(p)


WORKBENCH_XML_REFS = """<?xml version="1.0"?>
<config>
  <shared>
    <address>
      <entry name="web-srv-01"><ip-netmask>10.0.5.10/32</ip-netmask></entry>
      <entry name="db-gw"><ip-netmask>10.0.9.1/32</ip-netmask></entry>
      <entry name="net-10-0-5"><ip-netmask>10.0.5.0/24</ip-netmask></entry>
    </address>
    <address-group>
      <entry name="web-pool"><static><member>web-srv-01</member></static></entry>
    </address-group>
  </shared>
  <devices>
    <entry name="localhost.localdomain">
      <device-group/>
    </entry>
  </devices>
</config>
"""


@pytest.fixture
def workbench_xml_refs(tmp_path):
    """Config with a group referencing an address + a containing network,
    for the usage (where-used) and audit (overlap) spokes."""
    p = tmp_path / "config_refs.xml"
    p.write_text(WORKBENCH_XML_REFS, encoding="utf-8")
    return str(p)


WORKBENCH_XML_DG = """<?xml version="1.0"?>
<config>
  <shared>
    <address>
      <entry name="anchor"><ip-netmask>10.1.1.1/32</ip-netmask></entry>
    </address>
  </shared>
  <devices>
    <entry name="localhost.localdomain">
      <device-group>
        <entry name="dg1">
          <address>
            <entry name="dg-only"><ip-netmask>10.2.2.2/32</ip-netmask></entry>
          </address>
        </entry>
      </device-group>
    </entry>
  </devices>
</config>
"""


@pytest.fixture
def workbench_xml_dg(tmp_path):
    """A config with an object inside device-group dg1 (for move/rename)."""
    p = tmp_path / "config_dg.xml"
    p.write_text(WORKBENCH_XML_DG, encoding="utf-8")
    return str(p)


WORKBENCH_XML_TWO_DG = """<?xml version="1.0"?>
<config>
  <shared>
    <address>
      <entry name="anchor"><ip-netmask>10.1.1.1/32</ip-netmask></entry>
    </address>
  </shared>
  <devices>
    <entry name="localhost.localdomain">
      <device-group>
        <entry name="dg1">
          <address>
            <entry name="dg-only"><ip-netmask>10.2.2.2/32</ip-netmask></entry>
          </address>
        </entry>
        <entry name="dg2">
          <address/>
        </entry>
      </device-group>
    </entry>
  </devices>
</config>
"""


@pytest.fixture
def workbench_xml_two_dg(tmp_path):
    """Two sibling device-groups (dg1 holds 'dg-only', dg2 is empty). dg2 is a
    non-ancestor of dg1, so a move dg1 -> dg2 is a blocked, wrong-direction move."""
    p = tmp_path / "config_two_dg.xml"
    p.write_text(WORKBENCH_XML_TWO_DG, encoding="utf-8")
    return str(p)


WORKBENCH_XML_DANGLING = """<?xml version="1.0"?>
<config>
  <shared>
    <address>
      <entry name="web-srv-01"><ip-netmask>10.0.5.10/32</ip-netmask></entry>
    </address>
    <address-group>
      <entry name="web-pool">
        <static>
          <member>web-srv-01</member>
          <member>ghost-host</member>
        </static>
      </entry>
    </address-group>
  </shared>
  <devices><entry name="localhost.localdomain"><device-group/></entry></devices>
</config>
"""


@pytest.fixture
def workbench_xml_dangling(tmp_path):
    """Group 'web-pool' names a missing 'ghost-host' member (a dangling ref)."""
    p = tmp_path / "config_dangling.xml"
    p.write_text(WORKBENCH_XML_DANGLING, encoding="utf-8")
    return str(p)


WORKBENCH_XML_SCAN = """<?xml version="1.0"?>
<config>
  <shared>
    <address>
      <entry name="a-dup1"><ip-netmask>10.0.5.10/32</ip-netmask></entry>
      <entry name="a-dup2"><ip-netmask>10.0.5.10/32</ip-netmask></entry>
      <entry name="a-solo"><ip-netmask>10.0.9.1/32</ip-netmask></entry>
    </address>
    <service>
      <entry name="svc-443-a"><protocol><tcp><port>443</port></tcp></protocol></entry>
      <entry name="svc-443-b"><protocol><tcp><port>443</port></tcp></protocol></entry>
      <entry name="svc-8443"><protocol><tcp><port>8443</port></tcp></protocol></entry>
    </service>
    <address-group>
      <entry name="grp-a"><static><member>a-dup1</member><member>a-solo</member></static></entry>
      <entry name="grp-b"><static><member>a-dup1</member><member>a-solo</member></static></entry>
    </address-group>
  </shared>
  <devices>
    <entry name="localhost.localdomain">
      <device-group/>
    </entry>
  </devices>
</config>
"""


@pytest.fixture
def workbench_xml_scan(tmp_path):
    """Config-wide duplication + well-known-port fixture for the discovery spokes:
    two addresses share 10.0.5.10/32, two services share tcp/443 (also a
    predefined 'service-https' match), and grp-a/grp-b share an identical member
    set — one bucket each for the duplicates-scan and audit spokes (#95)."""
    p = tmp_path / "config_scan.xml"
    p.write_text(WORKBENCH_XML_SCAN, encoding="utf-8")
    return str(p)


WORKBENCH_XML_SHADOW = """<?xml version="1.0"?>
<config>
  <shared>
    <address>
      <entry name="anchor"><ip-netmask>10.1.1.1/32</ip-netmask></entry>
    </address>
  </shared>
  <devices>
    <entry name="localhost.localdomain">
      <device-group>
        <entry name="dg1">
          <address>
            <entry name="anchor"><ip-netmask>10.9.9.9/32</ip-netmask></entry>
          </address>
        </entry>
      </device-group>
    </entry>
  </devices>
</config>
"""


@pytest.fixture
def workbench_xml_shadow(tmp_path):
    """dg1 redefines the shared 'anchor' with a different value, so a shared-vs-dg1
    diff reports 'anchor' as *changed* — the changed-object path for the diff spoke."""
    p = tmp_path / "config_shadow.xml"
    p.write_text(WORKBENCH_XML_SHADOW, encoding="utf-8")
    return str(p)


WORKBENCH_XML_RULE = """<?xml version="1.0"?>
<config>
  <shared>
    <address>
      <entry name="web-srv-01"><ip-netmask>10.0.5.10/32</ip-netmask></entry>
      <entry name="db-gw"><ip-netmask>10.0.9.1/32</ip-netmask></entry>
    </address>
    <pre-rulebase>
      <security>
        <rules>
          <entry name="allow-web">
            <from><member>any</member></from>
            <to><member>any</member></to>
            <source><member>web-srv-01</member></source>
            <destination><member>any</member></destination>
            <service><member>any</member></service>
            <application><member>any</member></application>
            <action>allow</action>
          </entry>
        </rules>
      </security>
    </pre-rulebase>
  </shared>
  <devices>
    <entry name="localhost.localdomain">
      <device-group/>
    </entry>
  </devices>
</config>
"""


@pytest.fixture
def workbench_xml_rule(tmp_path):
    """Config with a pre-rulebase security rule 'allow-web' for the rule spoke."""
    p = tmp_path / "config_rule.xml"
    p.write_text(WORKBENCH_XML_RULE, encoding="utf-8")
    return str(p)


# Two sibling device-groups defining the SAME value under DIFFERENT names, each
# with its own local rule referencing its own copy — the divergent-name promote
# case (#154 --keep): `plan_merge_bucket` can't fix it (no shared-visible member),
# and a bare `promote` blocks on "bucket names diverge" until --keep/`keep=`
# says which name the survivor takes and DG-B's rule is repointed onto it.
WORKBENCH_XML_DIVERGENT_DUPS = """<?xml version="1.0"?>
<config>
  <shared>
    <address>
      <entry name="anchor"><ip-netmask>10.1.1.1/32</ip-netmask></entry>
    </address>
  </shared>
  <devices>
    <entry name="localhost.localdomain">
      <device-group>
        <entry name="dg1">
          <address>
            <entry name="h-web1"><ip-netmask>10.2.2.2/32</ip-netmask></entry>
          </address>
          <pre-rulebase>
            <security>
              <rules>
                <entry name="allow-h-web1">
                  <source><member>h-web1</member></source>
                  <destination><member>any</member></destination>
                  <service><member>any</member></service>
                  <application><member>any</member></application>
                  <action>allow</action>
                </entry>
              </rules>
            </security>
          </pre-rulebase>
        </entry>
        <entry name="dg2">
          <address>
            <entry name="web-primary"><ip-netmask>10.2.2.2/32</ip-netmask></entry>
          </address>
          <pre-rulebase>
            <security>
              <rules>
                <entry name="allow-web-primary">
                  <source><member>web-primary</member></source>
                  <destination><member>any</member></destination>
                  <service><member>any</member></service>
                  <application><member>any</member></application>
                  <action>allow</action>
                </entry>
              </rules>
            </security>
          </pre-rulebase>
        </entry>
      </device-group>
    </entry>
  </devices>
</config>
"""


@pytest.fixture
def session_with_divergent_dups(tmp_path) -> WorkbenchSession:
    """Selection pre-loaded with the two divergently-named `dg1`/`dg2` duplicates."""
    p = tmp_path / "config_divergent_dups.xml"
    p.write_text(WORKBENCH_XML_DIVERGENT_DUPS, encoding="utf-8")
    sess = WorkbenchSession(source=OfflineSource(str(p)), output_mode=OutputMode.SET)
    sess.add(SelectionItem(kind="address", name="h-web1", location="dg1"))
    sess.add(SelectionItem(kind="address", name="web-primary", location="dg2"))
    return sess
