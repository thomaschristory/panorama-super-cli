from __future__ import annotations

import pytest

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
