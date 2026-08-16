"""Regression tests for the isolated Life-Boat replay harness.

The harness runs the real agent against scripted turns. Its promise is that a run
cannot reach the live Telegram thread, the life-advisor memory store, or the
Dropbox-synced Obsidian vault. These tests cover the parts of that promise that are
pure logic; the run itself asserts the rest at the end of every execution.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


_REPLAY_PATH = Path(__file__).resolve().parents[2] / "scripts" / "lifeboat_sandbox_replay.py"


def _load_replay():
    spec = importlib.util.spec_from_file_location("lifeboat_sandbox_replay", _REPLAY_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


replay = _load_replay()


def test_filler_history_alternates_roles_and_has_the_requested_depth():
    history = replay._filler_history(5)
    assert len(history) == 10
    assert [turn["role"] for turn in history[:4]] == ["user", "assistant", "user", "assistant"]


def test_filler_history_is_empty_for_zero_or_negative_depth():
    assert replay._filler_history(0) == []
    assert replay._filler_history(-3) == []


def test_probe_message_is_synthetic_and_multi_threaded():
    """The probe reproduces the *shape* that fails - several threads at once plus a
    self-verdict - and is written from scratch. Noam's real conversation logs are never
    read into a scenario."""
    probe_text = replay.PROBE_TURNS[0]
    assert len(probe_text) > 150
    assert "גזר דין" in probe_text
    assert probe_text.count("וגם") >= 2


def test_baseline_strips_the_closing_anchor_from_the_assembled_bundle():
    """Without a control arm, "every reply ends on a question" proves nothing."""
    from gateway.lifeboat_psychology import _CLOSING_ANCHOR

    topic = "- keep it short\n- THE LAST THING YOU SAY IS ALWAYS AN OPENING BACK TO HIM."
    with_anchor = replay._ephemeral_prompt(topic, "משהו קטן", baseline=False)
    without = replay._ephemeral_prompt(topic, "משהו קטן", baseline=True)

    assert _CLOSING_ANCHOR in with_anchor
    assert _CLOSING_ANCHOR not in without
    assert "THE LAST THING YOU SAY" in with_anchor
    assert "THE LAST THING YOU SAY" not in without
    assert "keep it short" in without


def test_no_layout_strips_only_the_bubble_rules():
    topic = (
        "- Reply in Hebrew unless asked otherwise.\n"
        "- Never put a full coaching response in one long bubble.\n"
        "- Use the exact internal separator <<<SPLIT>>> between bubbles."
    )
    stripped = replay._ephemeral_prompt(topic, "משהו", no_layout=True)
    assert "Reply in Hebrew" in stripped
    assert "bubble" not in stripped
    assert "<<<SPLIT>>>" not in stripped


def test_memory_fingerprint_is_content_based_not_mtime_based():
    """An mtime check gave a false isolation alarm: the running gateway and the profile
    cron ticker open that database constantly. Only row content proves a sandbox write."""
    import inspect

    source = inspect.getsource(replay._memory_fingerprint)
    assert "st_mtime" not in source
    assert "memory_events" in source


def test_isolation_check_fails_when_a_plugin_loads_in_the_sandbox():
    """The Obsidian archive hook is a plugin; if any plugin loads, the vault is reachable."""
    before = {"memory_db": replay._memory_fingerprint(), "vault": {}}
    problems = replay._check_isolation(["obsidian-source-of-truth"], before)
    assert any("plugins loaded" in problem for problem in problems)


def test_endings_report_counts_questions_without_crashing_on_empty_replies(capsys):
    replay._report_endings(
        [
            {"assistant": "אני איתך.<<<SPLIT>>>מה קרה שם?"},
            {"assistant": "זה נשמע כבד."},
            {"assistant": ""},
        ]
    )
    out = capsys.readouterr().out
    assert "1/3 replies end on a question" in out
