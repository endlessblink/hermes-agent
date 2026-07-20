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

    def fake_context_files(
        cwd=None, skip_soul=False, context_length=None,
        allow_install_tree_fallback=False,
    ):
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


class TestMemoryPromptCompatibility:
    def test_legacy_memory_store_without_scoped_kwargs_does_not_crash(self):
        class LegacyMemoryStore:
            def format_for_system_prompt(self, target):
                return f"legacy {target} block"

        agent = _make_agent(
            _memory_store=LegacyMemoryStore(),
            _memory_enabled=True,
            _user_profile_enabled=True,
            _memory_scope_query="Botson",
            _memory_scope_cwd="/work/botson",
            _memory_scope_source="telegram",
        )

        with (
            patch("run_agent.load_soul_md", return_value=""),
            patch("run_agent.build_nous_subscription_prompt", return_value=""),
            patch("run_agent.build_environment_hints", return_value=""),
            patch("run_agent.build_context_files_prompt", return_value=""),
        ):
            volatile = build_system_prompt_parts(agent)["volatile"]

        assert "legacy memory block" in volatile
        assert "legacy user block" in volatile


class TestFlowStateGuidance:
    def test_injected_when_flowstate_tools_are_available(self):
        stable = _stable_prompt(_make_agent(valid_tool_names=["flowstate_create_task"]))

        assert "FlowState tool-use requirements" in stable
        assert "`flowstate_*` tool" in stable
        assert "`task-triage` preview" in stable

    def test_absent_without_flowstate_tools(self):
        stable = _stable_prompt(_make_agent(valid_tool_names=["read_file"]))

        assert "FlowState tool-use requirements" not in stable


class TestPersonalAssistantGuidance:
    def test_injected_when_capture_tool_is_available(self):
        stable = _stable_prompt(
            _make_agent(valid_tool_names=["personal_assistant_propose_capture"])
        )

        assert "Persistent personal assistant" in stable
        assert "queue a proposal" in stable
        assert "Do not silently save" in stable
        assert "smallest meaningful next step" in stable
        assert "end-of-day boundary" in stable
        assert "most compact supported visual form" in stable
        assert "Prefer an ordered action list" not in stable
        assert "protectively defer" in stable
        assert "matching context" in stable
        assert "consequential unknowns" in stable
        assert "next-move, session, or full-delivery" in stable
        assert "stopping evidence" in stable
        assert "Optional work" in stable
        assert "morning routine" in stable
        assert "uncategorized or unassigned FlowState tasks" in stable
        assert "preview-only organization proposal" in stable
        assert "explicitly approves" in stable
        assert "exact live FlowState read is authoritative" in stable
        assert "notes, memory, and past sessions as context only" in stable
        assert "personal_assistant_reconcile_inventory" in stable
        assert "source scope, capture time, stable item IDs" in stable
        assert "do not state or imply an exact combined count" in stable
        assert "complete=true" in stable
        assert "fresh=true" in stable
        assert "Never replace a failed or partial FlowState inventory" in stable
        assert "terminal code, ledger files, date-range brute force" in stable
        assert "personal_assistant_safety_review" in stable
        assert "protected item" in stable
        assert "all-clear" in stable

    def test_absent_without_personal_assistant_tools(self):
        stable = _stable_prompt(_make_agent(valid_tool_names=["flowstate_list_tasks"]))

        assert "Persistent personal assistant" not in stable


class TestDesktopQuestionnaireGuidance:
    def test_injected_for_desktop_platform(self):
        stable = _stable_prompt(_make_agent(platform="desktop"))

        assert "Hermes Desktop interactive questions" in stable
        assert '`type: "form"`' in stable
        assert "one question at a time" in stable
        assert "plain Markdown list" in stable
        assert "revise" in stable
        assert "long-text" in stable
        assert "allowCustomAnswer" in stable
        assert "customAnswerLabel" in stable
        assert "custom answer" in stable
        assert '`type: "task-breakdown"`' in stable
        assert "editable ordered steps" in stable
        assert "stopping evidence" in stable
        assert "regenerate the preview" in stable
        assert "exact preview" in stable
        assert "flowstate_subtask_batch" in stable
        assert "closest supported `hermes-ui` artifact" in stable
        assert "a `task-table` for a compact untimed daily plan" in stable
        assert "`day-timeline` for a one-day plan with clock times" in stable
        assert "a `week-planner` for every weekly or multi-day plan" in stable
        assert "Never use `mini-kanban` as a calendar" in stable
        assert "one short framing sentence" in stable
        assert "Never duplicate the artifact as prose" in stable
        assert "multi-day plan" in stable
        assert "agreed the outcomes" in stable
        assert "at most three planning items in prose" in stable
        assert "one day per date" in stable
        assert "per-block actions" in stable
        assert "live local date from the current turn" in stable
        assert "A `this week` planner must contain that date" in stable
        assert "visual artifact is the answer" in stable
        assert "daily-planning-list" not in stable
        assert "Do not merely offer or promise" in stable
        assert '"columns" and "rows"' in stable
        assert 'each row needs an `id` and `title`' in stable

    def test_injected_for_desktop_env(self, monkeypatch):
        monkeypatch.setenv("HERMES_DESKTOP", "1")
        stable = _stable_prompt(_make_agent())

        assert "Hermes Desktop interactive questions" in stable

    def test_absent_for_non_desktop_sessions(self, monkeypatch):
        monkeypatch.delenv("HERMES_DESKTOP", raising=False)
        stable = _stable_prompt(_make_agent(platform="telegram"))

        assert "Hermes Desktop interactive questions" not in stable
        assert "Hermes Telegram interactive questions" in stable
        assert "multi-day plan" in stable
        assert "native Telegram controls" in stable
        assert "one `week-planner` with one day per date" in stable
        assert "Never use `mini-kanban` as a calendar" in stable


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


class TestTelegramRichMessagesHint:
    """Verify that TELEGRAM_RICH_MESSAGES_HINT is conditionally included."""

    def test_base_hint_without_rich_messages(self, monkeypatch):
        """When rich_messages is False (default), only the base hint is used."""
        agent = _make_agent(platform="telegram")
        # Mock config to return rich_messages: false (default)
        with patch("hermes_cli.config.load_config_readonly") as mock_cfg:
            mock_cfg.return_value = {
                "platforms": {"telegram": {"extra": {"rich_messages": False}}}
            }
            stable = _stable_prompt(agent)
        # Base hint should be present
        assert "Standard Markdown is automatically converted" in stable
        # Rich-messages extension should NOT be present
        assert "lean into it" not in stable
        assert "task lists" not in stable

    def test_rich_hint_with_rich_messages_enabled(self, monkeypatch):
        """When rich_messages is True, the rich-messages extension is appended."""
        agent = _make_agent(platform="telegram")
        with patch("hermes_cli.config.load_config_readonly") as mock_cfg:
            mock_cfg.return_value = {
                "platforms": {"telegram": {"extra": {"rich_messages": True}}}
            }
            stable = _stable_prompt(agent)
        # Base hint should be present
        assert "Standard Markdown is automatically converted" in stable
        # Rich-messages extension should be present
        assert "lean into it" in stable
        assert "task lists" in stable
        assert "math/formulas" in stable

    def test_base_hint_without_config(self, monkeypatch):
        """When config has no telegram section, only base hint is used."""
        agent = _make_agent(platform="telegram")
        with patch("hermes_cli.config.load_config_readonly") as mock_cfg:
            mock_cfg.return_value = {}
            stable = _stable_prompt(agent)
        assert "Standard Markdown is automatically converted" in stable
        assert "lean into it" not in stable
