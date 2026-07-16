"""Runtime contract for the standalone Notion to FlowState bridge plugin."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import yaml


PLUGIN_NAME = "notion-flowstate-bridge"
TOOLSET_NAME = "notion_flowstate_bridge"
EXPECTED_TOOLS = {
    "notion_data_source_schema",
    "notion_data_source_list",
    "notion_page_get",
    "notion_mutation",
    "notion_flowstate_activate",
}


def _install_bridge_plugin(hermes_home: Path) -> None:
    source = (
        Path(__file__).parents[2]
        / "integrations"
        / "notion_flowstate_bridge"
    )
    shutil.copytree(source, hermes_home / "plugins" / PLUGIN_NAME)
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "plugins": {
                    "enabled": [PLUGIN_NAME],
                    "entries": {
                        PLUGIN_NAME: {
                            "config": {
                                "notion_data_source_id": "test-data-source",
                                "notion_writable_properties": ["Name", "Status"],
                            }
                        }
                    },
                }
            }
        ),
        encoding="utf-8",
    )


def _runtime_probe(
    tmp_path: Path,
    *,
    profile_name: str,
    notion_configured: bool,
    bridge_configured: bool = True,
) -> dict:
    state = "configured" if notion_configured else "unconfigured"
    state += "-scoped" if bridge_configured else "-missing-scope"
    fake_home = tmp_path / f"{profile_name}-{state}"
    if profile_name == "office-work":
        hermes_home = fake_home / ".hermes" / "profiles" / profile_name
    else:
        hermes_home = fake_home / ".hermes"
    hermes_home.mkdir(parents=True)
    _install_bridge_plugin(hermes_home)
    if not bridge_configured:
        (hermes_home / "config.yaml").write_text(
            yaml.safe_dump({"plugins": {"enabled": [PLUGIN_NAME]}}),
            encoding="utf-8",
        )

    script = f"""
import json
import model_tools
from hermes_cli.plugins import get_plugin_manager

manager = get_plugin_manager()
definitions = model_tools.get_tool_definitions(
    enabled_toolsets=[{TOOLSET_NAME!r}],
    quiet_mode=True,
)
plugin = manager._plugins[{PLUGIN_NAME!r}]
print(json.dumps({{'probe': {{
    'enabled': plugin.enabled,
    'error': plugin.error,
    'tool_names': [item['function']['name'] for item in definitions],
    'definitions': definitions,
}}}}, separators=(',', ':')))
"""
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(fake_home),
            "HERMES_HOME": str(hermes_home),
            "PYTHONPATH": str(Path(__file__).parents[2]),
        }
    )
    if notion_configured:
        env["NOTION_TOKEN"] = "test-notion-token"
    else:
        env.pop("NOTION_TOKEN", None)

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[2],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload_line = next(
        line for line in reversed(completed.stdout.splitlines()) if line.startswith('{"probe":')
    )
    return json.loads(payload_line)["probe"]


def test_user_installed_bridge_is_discovered_and_exposed_only_when_available(tmp_path):
    office = _runtime_probe(
        tmp_path,
        profile_name="office-work",
        notion_configured=True,
    )
    assert office["enabled"] is True, office["error"]
    assert set(office["tool_names"]) == EXPECTED_TOOLS

    default = _runtime_probe(
        tmp_path,
        profile_name="default",
        notion_configured=True,
    )
    assert default["enabled"] is True, default["error"]
    assert default["tool_names"] == []

    unconfigured = _runtime_probe(
        tmp_path,
        profile_name="office-work",
        notion_configured=False,
    )
    assert unconfigured["enabled"] is True, unconfigured["error"]
    assert unconfigured["tool_names"] == []

    missing_scope = _runtime_probe(
        tmp_path,
        profile_name="office-work",
        notion_configured=True,
        bridge_configured=False,
    )
    assert missing_scope["enabled"] is True, missing_scope["error"]
    assert missing_scope["tool_names"] == []


def test_activation_schema_allows_a_task_without_a_work_block(tmp_path):
    runtime = _runtime_probe(
        tmp_path,
        profile_name="office-work",
        notion_configured=True,
    )
    activation = next(
        item
        for item in runtime["definitions"]
        if item["function"]["name"] == "notion_flowstate_activate"
    )
    required = activation["function"]["parameters"]["required"]
    assert "operation_id" in required
    assert "page_id" in required
    assert "task" in required
    assert "work_block" not in required
