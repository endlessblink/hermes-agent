from unittest.mock import MagicMock, patch

import pytest

import cli as cli_module
from agent.skill_commands import scan_skill_commands
from cli import HermesCLI
from hermes_cli.commands import resolve_command


def _make_cli():
    cli_obj = HermesCLI.__new__(HermesCLI)
    cli_obj.config = {}
    cli_obj.console = MagicMock()
    cli_obj.agent = None
    cli_obj.conversation_history = []
    cli_obj.session_id = "sess-123"
    cli_obj._pending_input = MagicMock()
    cli_obj._agent_running = False
    return cli_obj


def _make_skill(skills_dir, name):
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"""---
name: {name}
description: Imported command wrapper
---

# {name}

Use this imported command wrapper.
""",
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _isolate_skill_command_registry(monkeypatch):
    monkeypatch.setattr(cli_module, "_skill_commands", None)
    monkeypatch.setattr("agent.skill_utils.get_external_skills_dirs", lambda: [])


@pytest.mark.parametrize(
    "name",
    [
        "codex-review",
        "ralph-loop-ralph-loop",
        "pr-review-toolkit-review-pr",
        "compound-engineering-playwright-test",
        "compound-engineering-triage",
        "commit-commands-commit-push-pr",
        "code-review-code-review",
        "hookify-hookify",
        "agent-sdk-dev-new-sdk-app",
        "coding-tutor-teach-me",
        "plugin-dev-create-plugin",
        "ralph-loop-cancel-ralph",
    ],
)
def test_imported_wrapper_skill_queues_by_canonical_slug(tmp_path, name):
    cli_obj = _make_cli()
    with patch("tools.skills_tool.SKILLS_DIR", tmp_path):
        _make_skill(tmp_path, name)
        scan_skill_commands()
        result = cli_obj.process_command(f"/{name} ship it")

    assert result is True
    cli_obj._pending_input.put.assert_called_once()
    queued = cli_obj._pending_input.put.call_args.args[0]
    assert "Imported command wrapper" in queued
    assert "ship it" in queued


def test_imported_wrapper_skill_does_not_pollute_native_registry():
    assert resolve_command("codex-review") is None
