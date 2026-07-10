"""Model-inferred fact extraction — the model-free parts (prompt, parse)."""

from agent.memory_extraction import (
    CATEGORIES,
    build_extraction_messages,
    parse_facts,
    render_transcript,
)


class TestRenderTranscript:
    def test_keeps_user_and_assistant_only(self):
        t = render_transcript([
            {"role": "system", "content": "SETUP"},
            {"role": "user", "content": "let's add dark mode"},
            {"role": "assistant", "content": "done"},
        ])
        assert "SETUP" not in t
        assert "user: let's add dark mode" in t
        assert "assistant: done" in t

    def test_keeps_the_tail_when_over_limit(self):
        msgs = [{"role": "user", "content": "x" * 100}] * 500
        msgs.append({"role": "user", "content": "FINAL DECISION"})
        t = render_transcript(msgs, limit=1000)
        assert "FINAL DECISION" in t  # freshest content survives truncation


class TestBuildMessages:
    def test_has_system_instructions_and_transcript(self):
        msgs = build_extraction_messages([{"role": "user", "content": "hi there friend"}])
        assert msgs[0]["role"] == "system"
        assert "JSON array" in msgs[0]["content"]
        assert "hi there friend" in msgs[1]["content"]

    def test_all_requested_categories_are_offered(self):
        # The categories the user asked for must be in the model's menu.
        for cat in ("subject", "lesson", "change", "decision", "rejected", "preference", "open_thread"):
            assert cat in CATEGORIES


class TestParseFacts:
    def test_parses_a_clean_array(self):
        raw = '[{"category":"subject","content":"Building the memory system for Hermes."}]'
        facts = parse_facts(raw)
        assert len(facts) == 1
        assert facts[0].category == "subject"
        assert "memory system" in facts[0].content

    def test_strips_code_fences(self):
        raw = '```json\n[{"category":"lesson","content":"Always run tests first."}]\n```'
        facts = parse_facts(raw)
        assert facts and facts[0].category == "lesson"

    def test_ignores_prose_around_the_array(self):
        raw = 'Here are the facts:\n[{"category":"change","content":"Add a dark mode toggle."}]\nHope that helps!'
        facts = parse_facts(raw)
        assert facts and facts[0].category == "change"

    def test_drops_unknown_categories(self):
        raw = '[{"category":"gossip","content":"nonsense"},{"category":"decision","content":"Chose SQLite."}]'
        facts = parse_facts(raw)
        assert [f.category for f in facts] == ["decision"]

    def test_dedups_identical_content(self):
        raw = '[{"category":"subject","content":"same"},{"category":"lesson","content":"same"}]'
        assert len(parse_facts(raw)) == 1

    def test_empty_and_garbage_return_nothing(self):
        assert parse_facts("") == []
        assert parse_facts("not json at all") == []
        assert parse_facts("[]") == []

    def test_missing_content_or_category_skipped(self):
        raw = '[{"category":"subject"},{"content":"orphan"},{"category":"change","content":"real one"}]'
        facts = parse_facts(raw)
        assert len(facts) == 1 and facts[0].content == "real one"
