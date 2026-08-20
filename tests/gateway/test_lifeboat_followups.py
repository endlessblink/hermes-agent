from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from gateway.lifeboat_followups import (
    ACHIEVEMENT_DELAY,
    FIRST_DELAY,
    LifeBoatFollowupBridge,
    arm_lifeboat_prompts,
    arm_achievement_prompt,
    arm_followup,
    build_continuation_prompt,
    build_continuation_guidance,
    build_lifeboat_coaching_guidance,
    build_lifeboat_coaching_prompt,
    cancel_followup,
    consume_followup_context,
    filter_lifeboat_toolsets,
    is_lifeboat_source,
    prepare_lifeboat_inbound_guidance,
)


NOW = datetime(2026, 8, 8, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def enable_delayed_followups_for_legacy_queue_tests(monkeypatch):
    monkeypatch.setenv("LIFEBOAT_PROACTIVE_FOLLOWUPS", "1")


def source(thread_id="2", profile="life-advisor"):
    return SimpleNamespace(
        platform=SimpleNamespace(value="telegram"),
        profile=profile,
        thread_id=thread_id,
        chat_id="-1004230590253",
    )


def test_lifeboat_turns_do_not_block_on_interactive_clarify():
    assert filter_lifeboat_toolsets(source(), ["memory", "clarify", "web"]) == [
        "memory",
        "web",
    ]


def test_other_sources_keep_clarify_available():
    assert "clarify" in filter_lifeboat_toolsets(
        source(thread_id="695", profile="office-work"), ["clarify"]
    )


class Router:
    def __init__(self, success=True):
        self.success = success
        self.calls = []

    async def deliver(self, content, targets, metadata=None):
        self.calls.append((content, targets, metadata))
        return {targets[0].to_string(): {"success": self.success}}


def test_lifeboat_source_is_profile_or_topic_scoped():
    assert is_lifeboat_source(source())
    assert is_lifeboat_source(source(thread_id="2", profile=None))
    assert not is_lifeboat_source(source(thread_id="695", profile="office-work"))


def test_question_arms_and_new_message_cancels(tmp_path):
    assert arm_followup(tmp_path, "telegram:-100:2", source(), "What should we do next?", now=NOW)
    assert (tmp_path / "state" / "lifeboat-followups.json").exists()
    assert cancel_followup(tmp_path, "telegram:-100:2")
    assert not cancel_followup(tmp_path, "telegram:-100:2")


def test_pending_reply_carries_language_but_never_the_old_reply(tmp_path):
    """A check-in must not replay any part of the assistant's earlier message.

    Storing an excerpt is how the 2026-08-09 22:30 check-in re-sent the fragment
    "כי אני כנראה עושה שלושה דברים שתוקעים: 1." — the sentence splitter cut on the
    period inside an enumerated list.  Language is the only thing worth keeping.
    """
    arm_followup(tmp_path, "session", source(), "רוצה לבחור את הצעד הבא?", now=NOW)
    pending = consume_followup_context(tmp_path, "session")
    assert pending is not None
    assert pending == {"language": "he"}
    assert "context" not in pending
    assert consume_followup_context(tmp_path, "session") is None


def test_continuation_prompt_requires_one_contextual_next_step():
    prompt = build_continuation_prompt(
        "כן",
        {"context": "רוצה לבחור את הצעד הבא?", "language": "he"},
    )
    assert "רוצה לבחור את הצעד הבא?" not in prompt
    assert "Respond in Hebrew" in prompt
    assert "do not pressure" in prompt
    assert "Do not reconstruct or restate what was said before the check-in" in prompt
    # The narrowing instructions that made it pick one angle and drop the rest.
    assert "stay with one concrete detail" not in prompt
    assert "at most one useful question" not in prompt
    assert "hypothesis" in prompt
    assert "אולי הקריטריון הוא" in prompt
    assert "Do not offer a menu of support options" in prompt
    assert "כן" not in build_continuation_guidance(
        {"context": "רוצה לבחור את הצעד הבא?", "language": "he"}
    )


def test_ordinary_lifeboat_prompt_keeps_inquiry_open():
    prompt = build_lifeboat_coaching_prompt("אני מרגיש שאני שוב נתקע באותו מקום")
    assert "Emotions are real experiences, not commands" in prompt
    assert "hypothesis" in prompt
    assert "אולי הקריטריון הוא" in prompt
    # It must ask for engagement with everything raised, not a single detail.
    assert "more than one" in prompt
    assert "dropping the rest" in prompt
    assert "choose one concrete detail" not in prompt
    assert "at most one useful question" not in prompt
    # And it must not script the reply's moves.
    assert "trying to solve" not in prompt
    assert "separate the verdict from the event" not in prompt
    assert "Do not offer a menu of support options" in prompt
    assert "either/or question that forces a choice" in prompt
    assert "אני מרגיש" not in build_lifeboat_coaching_guidance()


def test_natural_wrap_offers_one_permissioned_daily_summary(tmp_path):
    prompt = build_lifeboat_coaching_prompt("זה עזר לי, אפשר לעצור להיום")
    assert "The user appears to be wrapping up" in prompt
    assert "brief daily" in prompt
    assert "explicit approval before saving" in prompt
    assert "do not ask again" in prompt


def test_ordinary_turn_does_not_force_daily_summary():
    prompt = build_lifeboat_coaching_prompt("אני עדיין מנסה להבין מה קרה")
    assert "The user appears to be wrapping up" not in prompt


def test_inbound_guidance_updates_trajectory_and_consumes_pending_context(tmp_path):
    arm_followup(tmp_path, "session", source(), "רוצה להמשיך מכאן?", now=NOW)
    guidance = prepare_lifeboat_inbound_guidance(
        tmp_path,
        "session",
        "זה עזר לי, אפשר לעצור להיום",
    )
    assert "רוצה להמשיך מכאן?" not in guidance
    assert "The user appears to be wrapping up" in guidance
    assert consume_followup_context(tmp_path, "session") is None
    trajectory_state = (tmp_path / "state" / "lifeboat-psychology.json").read_text()
    assert "זה עזר לי" not in trajectory_state


def test_plain_completed_answer_does_not_arm(tmp_path):
    assert not arm_followup(tmp_path, "session", source(), "Done — I updated it.", now=NOW)


def test_achievement_prompt_is_rare_and_opt_in(tmp_path):
    assert arm_achievement_prompt(tmp_path, "session", source(), "You made real progress today.", now=NOW)
    assert not arm_achievement_prompt(
        tmp_path, "session", source(), "You completed another step.", now=NOW + timedelta(days=6)
    )
    assert not arm_achievement_prompt(tmp_path, "other", source(), "You completed another step.", now=NOW)


def test_proactive_scheduler_contract_arms_the_real_queue(tmp_path):
    outcomes = arm_lifeboat_prompts(tmp_path, "session", source(), "You completed a step.", now=NOW)
    assert outcomes == {"followup": False, "achievement": True}
    assert '"kind": "achievement"' in (tmp_path / "state" / "lifeboat-followups.json").read_text()


def test_proactive_scheduler_is_opt_in_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("LIFEBOAT_PROACTIVE_FOLLOWUPS", raising=False)
    outcomes = arm_lifeboat_prompts(tmp_path, "session", source(), "You completed a step.", now=NOW)
    assert outcomes == {"followup": False, "achievement": False}
    assert not (tmp_path / "state" / "lifeboat-followups.json").exists()


def test_crisis_turn_does_not_arm_routine_proactive_prompts(tmp_path):
    outcomes = arm_lifeboat_prompts(
        tmp_path,
        "session",
        source(),
        "Are you safe right now? You can contact ERAN 1201.",
        user_text="I might hurt myself tonight",
        now=NOW,
    )
    assert outcomes == {"followup": False, "achievement": False}
    assert not (tmp_path / "state" / "lifeboat-followups.json").exists()


@pytest.mark.asyncio
async def test_bridge_sends_one_optional_invitation_without_retrying(tmp_path):
    arm_followup(tmp_path, "session", source(), "Which option should I use?", now=NOW)
    router = Router()
    bridge = LifeBoatFollowupBridge(tmp_path, router)

    assert await bridge.deliver_once(now=NOW + FIRST_DELAY) is True
    assert len(router.calls) == 1
    assert "Just checking in" in router.calls[0][0]
    assert router.calls[0][1][0].chat_id == "-1004230590253"
    assert router.calls[0][1][0].thread_id == "2"

    assert await bridge.deliver_once(now=NOW + FIRST_DELAY + timedelta(days=1)) is False
    assert len(router.calls) == 1


@pytest.mark.asyncio
async def test_bridge_drops_stale_queue_when_delayed_contact_is_disabled(tmp_path, monkeypatch):
    arm_followup(tmp_path, "session", source(), "Which option should I use?", now=NOW)
    monkeypatch.delenv("LIFEBOAT_PROACTIVE_FOLLOWUPS", raising=False)
    router = Router()
    bridge = LifeBoatFollowupBridge(tmp_path, router)

    assert await bridge.deliver_once(now=NOW + FIRST_DELAY) is False
    assert router.calls == []
    assert '"items": {}' in (tmp_path / "state" / "lifeboat-followups.json").read_text()


@pytest.mark.asyncio
async def test_bridge_checks_in_in_hebrew_without_replaying_the_old_reply(tmp_path):
    arm_followup(tmp_path, "session", source(), "רוצה לבחור את הצעד הבא? נדבר על התוכנית מחר.", now=NOW)
    router = Router()
    bridge = LifeBoatFollowupBridge(tmp_path, router)

    assert await bridge.deliver_once(now=NOW + FIRST_DELAY) is True
    delivered = router.calls[0][0]
    assert "רוצה להמשיך מכאן" in delivered
    assert "Just checking in" not in delivered
    # No fragment of the assistant's own earlier message may reappear.
    assert "רוצה לבחור את הצעד הבא" not in delivered
    assert "נדבר על התוכנית מחר" not in delivered


@pytest.mark.asyncio
async def test_failed_delivery_retries_without_advancing_stage(tmp_path):
    arm_followup(tmp_path, "session", source(), "Can you send the details?", now=NOW)
    router = Router(success=False)
    bridge = LifeBoatFollowupBridge(tmp_path, router)

    assert await bridge.deliver_once(now=NOW + FIRST_DELAY) is False
    assert await bridge.deliver_once(now=NOW + FIRST_DELAY + timedelta(minutes=5)) is False
    assert len(router.calls) == 1


@pytest.mark.asyncio
async def test_bridge_respects_quiet_hours(tmp_path):
    quiet = NOW.replace(hour=22) + timedelta(hours=2)
    arm_followup(tmp_path, "session", source(), "Can you choose one?", now=quiet - FIRST_DELAY)
    router = Router()
    bridge = LifeBoatFollowupBridge(tmp_path, router)

    assert await bridge.deliver_once(now=quiet) is False
    assert router.calls == []


@pytest.mark.asyncio
async def test_bridge_delivers_achievement_prompt_once(tmp_path):
    arm_achievement_prompt(tmp_path, "session", source(), "You completed a step.", now=NOW)
    router = Router()
    bridge = LifeBoatFollowupBridge(tmp_path, router)

    assert await bridge.deliver_once(now=NOW + ACHIEVEMENT_DELAY) is True
    assert "real win" in router.calls[0][0]
    assert "achievements list" in router.calls[0][0]
    assert await bridge.deliver_once(now=NOW + ACHIEVEMENT_DELAY + timedelta(days=1)) is False
