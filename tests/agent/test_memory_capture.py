"""Deterministic (mechanical) fact capture from a conversation."""

from agent.memory_capture import mechanical_facts


REPO = "/home/endlessblink/.hermes/hermes-agent"


def _codex_turn():
    return [
        {
            "role": "assistant",
            "content": (
                f"```bash\ncodex exec --cd {REPO} --sandbox workspace-write - "
                "< /tmp/flowstate-prompt.md\n```"
            ),
        }
    ]


class TestWorkspaceFacts:
    def test_captures_repo_and_command(self):
        facts = mechanical_facts(_codex_turn())
        blob = " | ".join(f.content for f in facts)
        assert REPO in blob
        assert "codex exec" in blob
        assert "/tmp/flowstate-prompt.md" in blob

    def test_workspace_fact_is_categorized(self):
        facts = mechanical_facts(_codex_turn())
        assert any(f.category == "workspace" and REPO in f.content for f in facts)

    def test_no_repo_no_workspace_fact(self):
        facts = mechanical_facts([{"role": "user", "content": "hello there"}])
        assert not any(f.category == "workspace" for f in facts)


class TestStatementFacts:
    def test_captures_explicit_preference(self):
        facts = mechanical_facts([{"role": "user", "content": "I prefer short answers with no fluff"}])
        assert any(f.category == "user_pref" for f in facts)

    def test_captures_explicit_decision(self):
        facts = mechanical_facts([{"role": "user", "content": "we decided to use vite instead of webpack"}])
        assert any(f.category == "project" for f in facts)

    def test_ignores_assistant_statements(self):
        """Only the user's own words are captured, not the model's."""
        facts = mechanical_facts([{"role": "assistant", "content": "I prefer to help you"}])
        assert not facts

    def test_carries_source_message_id_when_present(self):
        facts = mechanical_facts([{"role": "user", "content": "I always run tests first", "id": 42}])
        prefs = [f for f in facts if f.category == "user_pref"]
        assert prefs and prefs[0].source_message_id == 42

    def test_chatter_is_not_captured(self):
        facts = mechanical_facts([{"role": "user", "content": "thanks, that worked great"}])
        assert not facts


class TestDedup:
    def test_identical_content_captured_once(self):
        msgs = _codex_turn() + _codex_turn()
        contents = [f.content for f in mechanical_facts(msgs)]
        assert len(contents) == len(set(contents))
