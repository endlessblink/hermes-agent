import json

from gateway.lifeboat_psychology import (
    LifeBoatSignals,
    build_signal_guidance,
    classify_lifeboat_signals,
    clear_lifeboat_trajectory,
    record_lifeboat_trajectory,
)


def test_classifies_current_turn_signals_without_diagnosis():
    assert classify_lifeboat_signals(
        "I keep thinking I am a failure and cannot stop overthinking"
    ) == LifeBoatSignals(thought_loop=True, self_criticism=True)
    assert classify_lifeboat_signals("אין לי כוח להמשיך") .possible_crisis
    assert classify_lifeboat_signals("I feel hopeless and empty").depressive_thoughts
    hebrew = classify_lifeboat_signals("אני תקוע בלופ של ביקורת עצמית")
    assert hebrew.thought_loop and hebrew.self_criticism


def test_signal_guidance_cautions_without_prescribing_a_move():
    text = "I am a failure and the same thought keeps looping"
    guidance = build_signal_guidance(text)
    assert "do not debate the thought" in guidance.lower()
    assert "generic reassurance" in guidance
    assert text not in guidance


def test_thought_record_guidance_advances_existing_material_without_looping():
    guidance = build_signal_guidance("אני תקוע בלופ של ביקורת עצמית")
    assert "do not ask for another sentence" in guidance.lower()
    assert "next missing stage" in guidance.lower()
    assert "event, verdict, and action-demand" in guidance


def test_guidance_never_dictates_the_canned_moves_that_broke_the_conversation():
    """Prescribed moves, not the model, produced Life-Boat's formulaic questions.

    The injected guidance used to order "ask what it is trying to solve, predict,
    protect, or avoid" and "offer one very small, concrete, optional next step".
    Those were emitted near-verbatim in Hebrew and rejected by the user on
    2026-08-10.  Guidance may say what to avoid; it must not script the reply.
    """
    banned = (
        "trying to solve",
        "predict, protect, or avoid",
        "small, concrete, optional next step",
        "Separate the person's identity",
        "reflect one concrete detail",
        "at most one useful question",
    )
    for text in (
        "",
        "I am a failure and the same thought keeps looping",
        "אני מרגיש ריק ואין לי כוח ואני שונא את עצמי",
        "I want to kill myself",
    ):
        guidance = build_signal_guidance(text)
        for phrase in banned:
            assert phrase not in guidance, f"{phrase!r} still dictated for {text!r}"


def test_guidance_asks_for_engagement_with_every_thread_raised():
    """The colleague/Berlin turns failed because one angle was picked."""
    guidance = build_signal_guidance("אני מרגיש ריק ואין לי כוח")
    assert "more than one" in guidance
    assert "dropping the rest" in guidance


def test_guidance_forbids_packaging_and_premature_closure():
    guidance = build_signal_guidance("נפגשתי איתה והיא עוברת לברלין")
    for phrase in ("do not package", "numbered breakdowns", "bulleted layers"):
        assert phrase in guidance
    assert "Do not close on a polished summary line" in guidance


def test_guidance_handles_ambiguity_and_decision_interest_without_self_blame():
    guidance = build_signal_guidance("היא לא ענתה ואני לא יודע אם בכלל יש עניין")
    assert "separate what is unknown" in guidance
    assert "silence is not evidence against them" in guidance
    assert "possible opener from genuine interest" in guidance
    assert "before offering advice" in guidance


def test_guidance_does_not_stack_directive_paragraphs_as_signals_accumulate():
    """Stacked directives are why replies became mountains of repeated interpretation."""
    plain = build_signal_guidance("סתם יום רגיל")
    loaded = build_signal_guidance("אני ריק, תקוע בלולאה, ושונא את עצמי")
    assert len(loaded) - len(plain) < 400


def test_neutral_guidance_offers_permissioned_daily_summary_without_self_prompting():
    guidance = build_signal_guidance("I think I am done for today")
    assert "optional daily summary" in guidance
    assert "ask permission" in guidance
    assert "never start it or prompt for it repeatedly" in guidance


def test_possible_crisis_prioritizes_direct_safety_and_human_support():
    guidance = build_signal_guidance("I want to kill myself")
    assert "immediate danger" in guidance
    assert "local emergency/crisis support" in guidance
    assert "ERAN 1201" in guidance
    assert "abstract coaching alone" in guidance


def test_trajectory_keeps_only_bounded_signal_state(tmp_path):
    first = record_lifeboat_trajectory(
        tmp_path,
        "telegram:life-advisor:123",
        "I want to hurt myself and I can't stop thinking",
    )
    assert first.recent_crisis_turns == 3
    assert first.recent_loop_turns == 3

    state = json.loads((tmp_path / "state" / "lifeboat-psychology.json").read_text())
    serialized = json.dumps(state)
    assert "I want to hurt myself" not in serialized
    assert "crisis" in serialized


def test_recent_crisis_context_survives_a_short_follow_up(tmp_path):
    trajectory = record_lifeboat_trajectory(tmp_path, "session", "I want to kill myself")
    guidance = build_signal_guidance("yes", trajectory)
    assert "possible safety concern appeared recently" in guidance
    assert "safe right now" in guidance


def test_recent_low_energy_guidance_does_not_present_a_support_menu(tmp_path):
    trajectory = record_lifeboat_trajectory(tmp_path, "session", "אין לי כוח היום")
    guidance = build_signal_guidance("אני עדיין תקוע", trajectory)

    assert "Do not offer a menu of support options" in guidance
    assert "either/or question that forces a choice" in guidance
    assert "understanding, a tiny action, or simply company" not in guidance


def test_session_reset_erases_trajectory(tmp_path):
    record_lifeboat_trajectory(tmp_path, "session", "I feel hopeless")
    assert clear_lifeboat_trajectory(tmp_path, "session")
    assert not clear_lifeboat_trajectory(tmp_path, "session")
