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
    ensure_lifeboat_open_response,
    finalize_lifeboat_response,
    filter_lifeboat_toolsets,
    is_lifeboat_source,
    lifeboat_response_issues,
    prepare_lifeboat_inbound_guidance,
    repair_repeated_lifeboat_response,
)


NOW = datetime(2026, 8, 8, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def enable_delayed_followups_for_legacy_queue_tests(monkeypatch):
    monkeypatch.setenv("LIFEBOAT_PROACTIVE_FOLLOWUPS", "1")


def source(thread_id="2", profile="life-advisor", chat_id="-1004230590253"):
    return SimpleNamespace(
        platform=SimpleNamespace(value="telegram"),
        profile=profile,
        thread_id=thread_id,
        chat_id=chat_id,
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
    assert "mode=attune" in prompt
    assert "Do not diagnose" in prompt
    assert "one open question" in prompt
    assert "tentative" in prompt
    assert "כן" not in build_continuation_guidance(
        {"context": "רוצה לבחור את הצעד הבא?", "language": "he"}
    )


def test_ordinary_lifeboat_prompt_keeps_inquiry_open():
    prompt = build_lifeboat_coaching_prompt("אני מרגיש שאני שוב נתקע באותו מקום")
    assert "mode=attune" in prompt
    assert "one concrete" in prompt
    assert "tentatively" in prompt
    assert "exactly two short sentences" in prompt
    assert "Only summarize or save anything when the user asks" in prompt
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


def test_response_contract_detects_long_closed_and_multiple_question_drafts():
    draft = "זה אומר שהערך שלך נקבע מבחוץ. אין פלא שזה מרגיש כבד. מה קורה? למה זה קורה?"
    issues = lifeboat_response_issues(draft, "אני מרגיש שהכול נהיה פסק דין על הערך שלי")
    assert "too_many_questions" in issues
    assert "premature_conclusion" not in issues


def test_response_repair_keeps_one_thread_and_opens_the_door():
    draft = "לסיכום, כל מה שקורה בעבודה ובזוגיות מוכיח שאתה לא מספיק טוב. אין פלא שזה כואב."
    repaired = ensure_lifeboat_open_response(
        draft,
        "אני נתקע בלופ של ביקורת עצמית על העבודה והזוגיות",
    )
    assert len(repaired) < len(draft) + 80
    assert repaired.count("?") == 0
    assert "אפשר להישאר עם זה עוד רגע" in repaired
    assert "לסיכום" not in repaired


def test_response_repair_trims_a_mountain_even_when_it_has_one_question():
    draft = (
        "אני שומע כמה זה כבד. אולי זה קשור לערך שלך. "
        "זה מתחבר גם לעבודה וגם לזוגיות. אולי אתה נושא את זה לבד. "
        "יכול להיות שכל תגובה נהיית פסק דין. אולי זה מפעיל פחד ישן. "
        "מה הכי נוכח אצלך עכשיו?"
    )
    repaired = ensure_lifeboat_open_response(draft, "אני מרגיש קבור תחת מחשבות")

    assert len(repaired) <= 720
    assert repaired.count("?") == 1
    assert len(repaired.split(".")) <= 3
    assert repaired.startswith("אני שומע כמה זה כבד.")
    assert repaired.endswith("מה הכי נוכח אצלך עכשיו?")


def test_response_contract_detects_and_reduces_numbered_mini_essay():
    draft = (
        "1. זה נוגע בערך שלך. 2. זה מתחבר לעבודה. "
        "3. זה מפעיל פחד מדחייה. 4. אתה נשאר עם זה לבד."
    )
    issues = lifeboat_response_issues(draft, "אני מרגיש שהכול נסגר עליי")
    repaired = ensure_lifeboat_open_response(draft, "אני מרגיש שהכול נסגר עליי")

    assert "list_heavy" in issues
    assert "1." not in repaired
    assert "2." not in repaired
    assert repaired.count("?") == 0
    assert len(repaired.split(".")) <= 2


def test_response_contract_does_not_flag_one_inline_hyphen():
    draft = "יש כאן כאב - לא מסקנה על מי שאני. מה הכי חי אצלך עכשיו?"
    assert "list_heavy" not in lifeboat_response_issues(draft, "כואב לי")


def test_explicit_revisit_request_keeps_the_previous_message_open():
    guidance = build_lifeboat_coaching_guidance(
        "בוא ננסה שוב לעבוד יחד עם ההודעה הקודמת, הפעם לאט וביחד"
    )
    assert "mode=revisit" in guidance
    assert "immediately preceding substantive message" in guidance
    assert "Do not defend" in guidance


def test_response_repair_does_not_reopen_an_explicit_pause():
    draft = "שמחה שהצלחנו לגעת בזה. נעצור להיום."
    assert ensure_lifeboat_open_response(draft, "זה עזר לי, נעצור להיום") == draft


def test_thought_loop_opening_does_not_force_a_question():
    repaired = ensure_lifeboat_open_response(
        "לסיכום, אתה צריך לפתור את זה עכשיו.",
        "אני נתקע שוב בלופ הזה ולא מצליח לצאת ממנו",
    )

    assert "?" not in repaired
    assert "אפשר להישאר עם זה עוד רגע" in repaired
    assert lifeboat_response_issues(repaired, "אני נתקע שוב בלופ הזה ולא מצליח לצאת ממנו") == ()


def test_repeated_response_repair_is_accountable_and_stays_open():
    repaired = repair_repeated_lifeboat_response(
        "נשמע שהכול נהיה פסק דין על הערך שלך.",
        "אני מרגיש שהכול נהיה פסק דין",
    )
    assert "חוזר על עצמי" in repaired
    assert repaired.count("?") == 1


def test_finalize_response_applies_duplicate_guard_across_turns(tmp_path):
    draft = "נשמע שהכול נהיה פסק דין על הערך שלך. מה הכי חי אצלך עכשיו?"
    first = finalize_lifeboat_response(tmp_path, "session", draft, "אני מרגיש שהכול נהיה פסק דין")
    second = finalize_lifeboat_response(tmp_path, "session", draft, "אני מרגיש שהכול נהיה פסק דין")

    assert first == draft
    assert "חוזר על עצמי" in second
    assert second.count("?") == 1


def test_topic_two_in_another_chat_is_not_lifeboat():
    assert not is_lifeboat_source(source(chat_id="-1009999999999", profile="office-work"))


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
async def test_bridge_drops_stale_queue_when_delayed_contact_is_disabled(tmp_path, monkeypatch):
    arm_followup(tmp_path, "session", source(), "Which option should I use?", now=NOW)
    monkeypatch.delenv("LIFEBOAT_PROACTIVE_FOLLOWUPS", raising=False)
    router = Router()
    bridge = LifeBoatFollowupBridge(tmp_path, router)

    assert await bridge.deliver_once(now=NOW + FIRST_DELAY) is False
    assert router.calls == []
    assert '"items": {}' in (tmp_path / "state" / "lifeboat-followups.json").read_text()


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
