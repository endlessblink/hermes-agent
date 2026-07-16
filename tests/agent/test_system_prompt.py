"""Tests for agent/system_prompt.py — context-file cwd wiring."""

from types import SimpleNamespace
from unittest.mock import patch

from agent.system_prompt import build_system_prompt_parts


def _make_agent(**overrides):
    base = dict(
        load_soul_identity=False,
        skip_context_files=False,
        valid_tool_names=[],
        _task_completion_guidance=False,
        _tool_use_enforcement=False,
        _environment_probe=False,
        _kanban_worker_guidance="",
        _memory_store=None,
        _memory_manager=None,
        model="",
        provider="",
        platform="",
        pass_session_id=False,
        session_id="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _captured_context_cwd(agent):
    """The cwd build_system_prompt_parts hands to build_context_files_prompt."""
    captured = {}

    def fake_context_files(cwd=None, skip_soul=False, context_length=None):
        captured["cwd"] = cwd
        return ""

    with (
        patch("run_agent.load_soul_md", return_value=""),
        patch("run_agent.build_nous_subscription_prompt", return_value=""),
        patch("run_agent.build_environment_hints", return_value=""),
        patch("run_agent.build_context_files_prompt", side_effect=fake_context_files),
    ):
        build_system_prompt_parts(agent)
    return captured["cwd"]


class TestContextFileCwd:
    def test_none_when_terminal_cwd_unset(self, monkeypatch):
        # Unset → None, so discovery falls back to the launch dir inside
        # build_context_files_prompt (the local-CLI #19242 contract).
        monkeypatch.delenv("TERMINAL_CWD", raising=False)
        assert _captured_context_cwd(_make_agent()) is None

    def test_configured_dir_when_terminal_cwd_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        assert _captured_context_cwd(_make_agent()) == tmp_path


def _stable_prompt(agent):
    with (
        patch("run_agent.load_soul_md", return_value=""),
        patch("run_agent.build_nous_subscription_prompt", return_value=""),
        patch("run_agent.build_environment_hints", return_value=""),
        patch("run_agent.build_context_files_prompt", return_value=""),
    ):
        return build_system_prompt_parts(agent)["stable"]


def _init_code_repo(path):
    """A git repo that actually holds code — the coding posture requires a source
    file (or manifest), not a bare ``.git`` (a prose/notes repo stays general)."""
    import subprocess

    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    (path / "main.py").write_text("print('hi')\n")


class TestFlowStateBreakdownGuidance:
    def test_desktop_flowstate_tools_receive_stable_preview_guidance(self):
        stable = _stable_prompt(_make_agent(
            platform="desktop",
            valid_tool_names=["flowstate_list_subtasks", "flowstate_subtask_batch"],
        ))

        assert "fresh `flowstate_list_subtasks`" in stable
        assert "stable proposalId" in stable
        assert "Editing is never approval" in stable
        assert "approvalRequest" in stable
        assert "flowstate-mutation-decision" in stable
        assert "type=task-breakdown" in stable
        assert "canonicalApproval" in stable
        assert "Do not print the artifact as generic JSON" in stable
        assert "invalid_existing_subtasks" in stable
        assert "never overwrite or synthesize" in stable
        assert "read every relevant page" in stable
        assert "nextCursor" in stable

    def test_guidance_is_absent_without_both_flowstate_tools(self):
        stable = _stable_prompt(_make_agent(
            platform="desktop", valid_tool_names=["flowstate_list_subtasks"]
        ))
        assert "Editing is never approval" not in stable

    def test_desktop_backend_env_receives_guidance(self, monkeypatch):
        monkeypatch.setenv("HERMES_DESKTOP", "1")
        stable = _stable_prompt(_make_agent(
            valid_tool_names=["flowstate_list_subtasks", "flowstate_subtask_batch"]
        ))
        assert "Editing is never approval" in stable


class TestNotionFlowStateBridgeGuidance:
    def test_desktop_bridge_tools_receive_interactive_approval_guidance(self):
        stable = _stable_prompt(_make_agent(
            platform="desktop",
            valid_tool_names=["notion_mutation", "notion_flowstate_activate"],
        ))
        assert "Notion pages as the project source of truth" in stable
        assert "type=notion-mutation-preview" in stable
        assert "copy its exact `approval_request` unchanged" in stable
        assert "ordinary chat reply is not approval" in stable
        assert "Starting work and changing Notion status are separate" in stable

    def test_bridge_guidance_is_absent_without_both_mutation_tools(self):
        stable = _stable_prompt(_make_agent(
            platform="desktop", valid_tool_names=["notion_mutation"]
        ))
        assert "type=notion-mutation-preview" not in stable


class TestCrossSourceInventoryGuidance:
    def test_reconciliation_tool_forbids_prose_arithmetic(self):
        stable = _stable_prompt(_make_agent(
            platform="desktop", valid_tool_names=["task_inventory_reconcile"]
        ))
        assert "before reporting a cross-source task count" in stable
        assert "Never add source counts in prose" in stable
        assert "verified=false" in stable
        assert "connector proves end-of-pagination" in stable

    def test_guidance_is_absent_without_reconciliation_tool(self):
        stable = _stable_prompt(_make_agent(
            platform="desktop", valid_tool_names=["notion_data_source_list"]
        ))
        assert "Never add source counts in prose" not in stable


class TestCodingContextBlock:
    def test_injected_when_active(self, monkeypatch, tmp_path):
        _init_code_repo(tmp_path)
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        agent = _make_agent(valid_tool_names=["read_file"], platform="cli")
        stable = _stable_prompt(agent)
        assert "coding agent" in stable
        assert "Workspace" in stable

    def test_absent_when_off(self, monkeypatch, tmp_path):
        _init_code_repo(tmp_path)
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        agent = _make_agent(valid_tool_names=["read_file"], platform="cli")
        # Drive the real path: force the resolved mode to "off" via config.
        with patch("agent.coding_context._coding_mode", return_value="off"):
            stable = _stable_prompt(agent)
        assert "coding agent" not in stable

    def test_absent_without_tools(self, monkeypatch, tmp_path):
        _init_code_repo(tmp_path)
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        agent = _make_agent(valid_tool_names=[], platform="cli")
        assert "coding agent" not in _stable_prompt(agent)
