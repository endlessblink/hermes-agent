"""Unit tests for the suggestion discipline state layer (agent/suggestion_gate.py)."""

import time  # noqa: F401 - used via time.time

from agent import suggestion_gate as sg

DAY = 86400.0
T0 = 1_800_000_000.0  # fixed reference instant


class TestRuleStrengthLadder:
    def test_reasoned_rejection_is_immediately_permanent(self, tmp_path):
        out = sg.save_rejection(tmp_path, "evening-appliance-check",
                                reason="laundry closes before evening", now=T0)
        assert out["action"] == "rule_saved"
        assert out["strength"] == "permanent"
        assert "no more evening appliance check" in out["ack"]

    def test_unreasoned_rejection_is_provisional_then_permanent_next_day(self, tmp_path):
        first = sg.save_rejection(tmp_path, "startup-reminder", now=T0)
        assert first["strength"] == "provisional"
        same_day = sg.save_rejection(tmp_path, "startup-reminder", now=T0 + 3600)
        assert same_day["strength"] == "provisional"
        next_day = sg.save_rejection(tmp_path, "startup-reminder", now=T0 + DAY)
        assert next_day["strength"] == "permanent"

    def test_reason_added_later_upgrades_to_permanent(self, tmp_path):
        sg.save_rejection(tmp_path, "meeting-prep", now=T0)
        out = sg.save_rejection(tmp_path, "meeting-prep", reason="I prep alone", now=T0 + 60)
        assert out["strength"] == "permanent"

    def test_mood_flavored_rejection_mutes_today_without_rule(self, tmp_path):
        out = sg.save_rejection(tmp_path, "anything", mood_flavored=True, now=T0)
        assert out["action"] == "mood_mute"
        assert sg.load_rules(tmp_path) == []
        assert sg.get_mood(tmp_path, now=T0) is not None

    def test_remove_rule(self, tmp_path):
        sg.save_rejection(tmp_path, "x-class", reason="r", now=T0)
        assert sg.remove_rule(tmp_path, "x-class") is True
        assert sg.remove_rule(tmp_path, "x-class") is False
        assert sg.load_rules(tmp_path) == []


class TestMoodExpiry:
    def test_mood_expires_at_local_midnight(self, tmp_path):
        sg.set_mood(tmp_path, note="wiped out", now=T0)
        assert sg.get_mood(tmp_path, now=T0) is not None
        assert sg.get_mood(tmp_path, now=T0 + 2 * DAY) is None


class TestDailyCap:
    def test_counter_increments_and_resets_next_day(self, tmp_path):
        assert sg.suggestions_today(tmp_path, now=T0) == 0
        assert sg.record_suggestion(tmp_path, now=T0) == 1
        assert sg.record_suggestion(tmp_path, now=T0) == 2
        assert sg.suggestions_today(tmp_path, now=T0) == 2
        assert sg.suggestions_today(tmp_path, now=T0 + 2 * DAY) == 0


class TestDisciplineBlock:
    def test_block_contains_time_cap_rules_and_mood(self, tmp_path):
        sg.save_rejection(tmp_path, "evening-appliance-check",
                          reason="laundry closes before evening", now=T0)
        sg.set_mood(tmp_path, note="not feeling well", now=T0)
        sg.record_suggestion(tmp_path, now=T0)
        block = sg.build_discipline_block(tmp_path, now=T0)
        assert "# Suggestion discipline" in block
        assert "1/2" in block
        assert "evening-appliance-check" in block
        assert "laundry closes before evening" in block
        assert "MOOD" in block
        assert "small-wins" in block
        assert "suggestion_rule_save" in block
        assert len(block) < 2000

    def test_block_without_state_is_compact_and_silent_on_mood(self, tmp_path):
        block = sg.build_discipline_block(tmp_path, now=T0)
        assert "MOOD" not in block
        assert "0/2" in block
        assert len(block) < 1200

    def test_rule_overflow_is_summarized(self, tmp_path):
        for i in range(14):
            sg.save_rejection(tmp_path, f"class-{i}", reason="r", now=T0)
        block = sg.build_discipline_block(tmp_path, now=T0)
        assert "(+4 more" in block

    def test_corrupt_state_files_fail_open(self, tmp_path):
        for name in (sg.RULES_FILENAME, sg.MOOD_FILENAME, sg.COUNTER_FILENAME):
            (tmp_path / name).write_text("{corrupt", encoding="utf-8")
        assert sg.load_rules(tmp_path) == []
        assert sg.get_mood(tmp_path, now=T0) is None
        assert sg.suggestions_today(tmp_path, now=T0) == 0
        assert "# Suggestion discipline" in sg.build_discipline_block(tmp_path, now=T0)
