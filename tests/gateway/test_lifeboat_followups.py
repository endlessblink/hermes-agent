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
    repair_lifeboat_closure,
    cancel_followup,
    consume_followup_context,
    filter_lifeboat_toolsets,
    is_lifeboat_source,
    prepare_lifeboat_inbound_guidance,
)


NOW = datetime(2026, 8, 8, 10, 0, tzinfo=timezone.utc)


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


def test_pending_reply_consumes_context_for_continuation(tmp_path):
    arm_followup(tmp_path, "session", source(), "רוצה לבחור את הצעד הבא?", now=NOW)
    assert consume_followup_context(tmp_path, "session") == {
        "context": "רוצה לבחור את הצעד הבא?",
        "language": "he",
    }
    assert consume_followup_context(tmp_path, "session") is None


def test_continuation_prompt_requires_one_contextual_next_step():
    prompt = build_continuation_prompt(
        "כן",
        {"context": "רוצה לבחור את הצעד הבא?", "language": "he"},
    )
    assert "רוצה לבחור את הצעד הבא?" in prompt
    assert "Respond in Hebrew" in prompt
    assert "at most one useful question" in prompt
    assert "Do not diagnose" in prompt
    assert "do not pressure" in prompt
    assert "stay with one concrete detail" in prompt
    assert "leave one open door" in prompt
    assert "The reflection is a hypothesis, not the answer" in prompt
    assert "אולי הקריטריון הוא" in prompt
    assert "one real question" in prompt
    assert "כן" not in build_continuation_guidance(
        {"context": "רוצה לבחור את הצעד הבא?", "language": "he"}
    )


def test_ordinary_lifeboat_prompt_keeps_inquiry_open():
    prompt = build_lifeboat_coaching_prompt("אני מרגיש שאני שוב נתקע באותו מקום")
    assert "choose one concrete detail" in prompt
    assert "open one door for further exploration" in prompt
    assert "Emotions are real experiences, not commands" in prompt
    assert "thought loop" in prompt
    assert "self-criticism" in prompt
    assert "only summarize or make an action plan when the user asks" in prompt
    assert "The reflection must remain a hypothesis" in prompt
    assert "אולי הקריטריון הוא" in prompt
    assert "one genuine question" in prompt
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


def test_closure_repair_returns_agency_to_a_locked_insight():
    repaired = repair_lifeboat_closure(
        "אז אולי הקריטריון הוא: אין צורך להרגיש לבד.",
        "אבל אם אני לא יודע מראש איך היום ייראה?",
    )
    assert repaired.endswith("אני לא רוצה לקבוע את זה במקומך — איך זה פוגש אותך?")


def test_closure_repair_does_not_touch_open_or_wrapping_turns():
    assert repair_lifeboat_closure("מה הכי חי בזה עכשיו?", "אני עדיין בודק") == "מה הכי חי בזה עכשיו?"
    wrapped = "אין צורך להרגיש לבד."
    assert repair_lifeboat_closure(wrapped, "תודה, אפשר לעצור להיום") == wrapped


def test_inbound_guidance_updates_trajectory_and_consumes_pending_context(tmp_path):
    arm_followup(tmp_path, "session", source(), "רוצה להמשיך מכאן?", now=NOW)
    guidance = prepare_lifeboat_inbound_guidance(
        tmp_path,
        "session",
        "זה עזר לי, אפשר לעצור להיום",
    )
    assert "The topic was: רוצה להמשיך מכאן?" in guidance
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
async def test_bridge_sends_first_then_schedules_second(tmp_path):
    arm_followup(tmp_path, "session", source(), "Which option should I use?", now=NOW)
    router = Router()
    bridge = LifeBoatFollowupBridge(tmp_path, router)

    assert await bridge.deliver_once(now=NOW + FIRST_DELAY) is True
    assert len(router.calls) == 1
    assert "Just checking in" in router.calls[0][0]
    assert router.calls[0][1][0].chat_id == "-1004230590253"
    assert router.calls[0][1][0].thread_id == "2"

    assert await bridge.deliver_once(now=NOW + FIRST_DELAY + timedelta(hours=23)) is False
    assert await bridge.deliver_once(now=NOW + FIRST_DELAY + timedelta(days=1)) is True
    assert len(router.calls) == 2
    assert "Happy to pick this back up" in router.calls[1][0]


@pytest.mark.asyncio
async def test_bridge_matches_hebrew_and_keeps_context(tmp_path):
    arm_followup(tmp_path, "session", source(), "רוצה לבחור את הצעד הבא? נדבר על התוכנית מחר.", now=NOW)
    router = Router()
    bridge = LifeBoatFollowupBridge(tmp_path, router)

    assert await bridge.deliver_once(now=NOW + FIRST_DELAY) is True
    assert "רוצה להמשיך מכאן" in router.calls[0][0]
    assert "רוצה לבחור את הצעד הבא?" in router.calls[0][0]
    assert "Just checking in" not in router.calls[0][0]


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
