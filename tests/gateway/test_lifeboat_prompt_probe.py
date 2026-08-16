"""Regression tests for the Life-Boat instruction linter.

The 2026-08-13 failure was not a code defect. The Telegram topic prompt prescribed a
one-line verdict for every reply while the conversation contract forbade closing, and
nothing anywhere compared the two. These tests lock in the check that would have caught
it, using the actual before/after wording from that incident.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


_PROBE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "lifeboat_prompt_probe.py"


def _load_probe():
    spec = importlib.util.spec_from_file_location("lifeboat_prompt_probe", _PROBE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load_probe()


# The topic prompt as it read when it truncated his replies into verdicts.
BROKEN_TOPIC_PROMPT = (
    "Do not force a 3-part structure on every answer; use only distinct, "
    "non-repeating bubbles. For draft review: one-line verdict/trap, then the "
    "revised draft; third bubble only if there is a genuinely new next step."
)

# The same rule after being scoped to an explicit review request.
SCOPED_TOPIC_PROMPT = (
    "A one-line verdict, a trap-naming line, or a revised draft belongs only where "
    "he has explicitly asked you to review a draft, a decision, or a plan."
)

OPEN_STANCE = (
    "Do not close on a polished summary line; leave the thread alive. Do not "
    "over-summarize. Offer at most one tentative opening question and wait for him."
)


def test_lint_catches_the_2026_08_13_contradiction():
    findings, _ = probe._lint(BROKEN_TOPIC_PROMPT + "\n\n" + OPEN_STANCE)
    assert any(finding.startswith("CONTRADICTION") for finding in findings)


def test_lint_accepts_a_shape_rule_that_is_scoped_to_an_explicit_request():
    findings, _ = probe._lint(SCOPED_TOPIC_PROMPT + "\n\n" + OPEN_STANCE)
    assert not any(finding.startswith("CONTRADICTION") for finding in findings)


def test_lint_warns_when_nothing_asks_the_assistant_to_stay_open():
    findings, _ = probe._lint(SCOPED_TOPIC_PROMPT)
    assert any("no open-stance" in finding for finding in findings)


def test_lint_counts_prohibitions_against_positive_directives():
    """Prohibitions decay with conversation depth; positive instructions hold.

    The ratio is reported so a bundle cannot quietly drift back into a wall of "do not".
    """
    _, counts = probe._lint(OPEN_STANCE)
    assert counts["prohibitions"] >= 2
    assert counts["chars"] == len(OPEN_STANCE)


def test_asymmetry_reports_a_rule_present_on_only_one_telegram_surface():
    """Every anti-closure rule living in the DM prompt and none in the topic prompt is
    how the regression got in: the support conversation happens in the topic."""
    asymmetry = probe._asymmetry(
        "leave the thread alive; do not close on a summary",
        "be concise and concrete",
    )
    assert any("topic only" in line for line in asymmetry)


def test_asymmetry_is_silent_when_both_surfaces_agree():
    shared = "leave the thread alive; do not over-summarize; wait for the user"
    assert probe._asymmetry(shared, shared) == []


@pytest.mark.parametrize(
    "chat_id,thread_id,expected",
    [
        ("-1004230590253", "2", True),
        ("-1004230590253", "7", False),
        ("-1009999999999", "2", False),
    ],
)
def test_probe_identity_constants_match_the_real_support_thread(
    chat_id: str, thread_id: str, expected: bool
):
    """A thread id is only unique inside one chat, so both halves are required.

    A profile-name-only check silently disables every Life-Boat behaviour while looking
    correct, because the gateway runs the root profile for these turns.
    """
    from gateway.lifeboat_followups import is_lifeboat_source
    from types import SimpleNamespace

    source = SimpleNamespace(
        platform=SimpleNamespace(value="telegram"),
        profile="default",
        chat_id=chat_id,
        thread_id=thread_id,
    )
    assert is_lifeboat_source(source) is expected
    assert probe.LIFEBOAT_CHAT_ID == "-1004230590253"
    assert probe.LIFEBOAT_THREAD_ID == "2"
