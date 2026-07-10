"""The pre-action gate: confirm the project before acting, fail-open, no loops."""

import types

import pytest

from agent import lane_gate


class TestIsVagueContinuation:
    @pytest.mark.parametrize("msg", [
        "let's continue",
        "codex finished, can you check?",
        "keep going",
        "continue what we started",
        "קודקס סיים, אתה יכול לבדוק?",
        "תמשיך מאיפה שעצרנו",
        "check it",
    ])
    def test_vague_messages_match(self, msg):
        assert lane_gate.is_vague_continuation(msg)

    @pytest.mark.parametrize("msg", [
        "add a dark-mode toggle to the header component",
        "in the hermes-agent repo, run the tests",
        "fix the bug in /home/user/proj/main.py",
        "what is 2 + 2",
        "write a haiku about the sea",
    ])
    def test_specific_instructions_do_not_match(self, msg):
        assert not lane_gate.is_vague_continuation(msg)

    def test_long_detailed_message_is_not_vague_even_with_trigger_word(self):
        msg = ("continue by refactoring the authentication module so that it uses "
               "the new token store, then update all the call sites and add tests "
               "for the expiry edge cases and the refresh path across sessions")
        assert not lane_gate.is_vague_continuation(msg)

    def test_empty_and_non_string_safe(self):
        assert not lane_gate.is_vague_continuation("")
        assert not lane_gate.is_vague_continuation(None)  # type: ignore[arg-type]


class TestShouldArm:
    def test_arms_on_vague_with_no_lane(self):
        assert lane_gate.should_arm("let's continue", has_confirmed_lane=False)

    def test_does_not_arm_when_lane_is_known(self):
        # We already know the project this session — nothing to confirm.
        assert not lane_gate.should_arm("let's continue", has_confirmed_lane=True)

    def test_does_not_arm_on_specific_instruction(self):
        assert not lane_gate.should_arm("add a login button", has_confirmed_lane=False)


class TestEvaluate:
    @pytest.mark.parametrize("tool", ["read_file", "write_file", "patch", "search_files"])
    def test_repo_tools_blocked(self, tool):
        assert lane_gate.evaluate(tool, {}) is not None

    @pytest.mark.parametrize("tool", ["clarify", "session_search", "read_terminal",
                                       "process", "vault_status", "read_note"])
    def test_evidence_and_escape_tools_allowed(self, tool):
        assert lane_gate.evaluate(tool, {}) is None

    def test_readonly_terminal_allowed(self):
        for cmd in ["git status", "git diff --stat", "ls -la", "cat README.md", "pwd"]:
            assert lane_gate.evaluate("terminal", {"command": cmd}) is None

    def test_mutating_terminal_blocked(self):
        for cmd in ["rm -rf build", "git commit -m x", "npm run build", "echo x > f"]:
            assert lane_gate.evaluate("terminal", {"command": cmd}) is not None


class TestGateBlockMessage:
    def _agent(self, armed, satisfied):
        return types.SimpleNamespace(_lane_gate_armed=armed, _lane_gate_satisfied=satisfied)

    def test_blocks_when_armed_and_unsatisfied(self):
        msg = lane_gate.gate_block_message(self._agent(True, False), "write_file", {"path": "x"})
        assert msg and "clarify" in msg.lower()

    def test_does_not_block_when_not_armed(self):
        assert lane_gate.gate_block_message(self._agent(False, False), "write_file", {}) is None

    def test_does_not_block_once_satisfied(self):
        assert lane_gate.gate_block_message(self._agent(True, True), "write_file", {}) is None

    def test_never_blocks_clarify_itself(self):
        # The escape hatch must always be reachable.
        assert lane_gate.gate_block_message(self._agent(True, False), "clarify", {}) is None

    def test_fails_open_on_missing_attributes(self):
        # An agent object without the gate flags must not raise or block.
        assert lane_gate.gate_block_message(object(), "write_file", {}) is None

    def test_fails_open_on_bad_args(self):
        # A gate bug must never brick a tool call.
        assert lane_gate.gate_block_message(self._agent(True, False), "terminal", None) is None
