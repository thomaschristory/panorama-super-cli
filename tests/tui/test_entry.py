from __future__ import annotations

from psc.cli.app import app
from psc.cli.workbench_cmds import build_session
from psc.tui.state import OutputMode


def test_build_session_from_offline_config(workbench_xml: str) -> None:
    sess = build_session(config_file=workbench_xml, profile=None, output_mode=OutputMode.SET)
    assert any(a.name == "web-srv-01" for a in sess.working_snapshot.addresses)
    assert sess.output_mode is OutputMode.SET


def test_workbench_command_is_registered() -> None:
    names = {cmd.name for cmd in app.registered_commands}
    assert "workbench" in names
    assert "w" in names
