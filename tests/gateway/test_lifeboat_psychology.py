import json

from gateway.lifeboat_psychology import (
    LifeBoatSignals,
    build_signal_guidance,
    classify_lifeboat_signals,
    clear_lifeboat_trajectory,
    record_lifeboat_response_fingerprint,
    record_lifeboat_trajectory,
    select_lifeboat_turn_policy,
)


def test_classifies_current_turn_signals_without_diagnosis():
    assert classify_lifeboat_signals(
        "I keep thinking I am a failure and cannot stop overthinking"
    ) == LifeBoatSignals(thought_loop=True, self_criticism=True)
    assert classify_lifeboat_signals("אין לי כוח להמשיך") .possible_crisis
    assert classify_lifeboat_signals("I feel hopeless and empty").depressive_thoughts
    hebrew = classify_lifeboat_signals("אני תקוע בלופ של ביקורת עצמית")
    assert hebrew.thought_loop and hebrew.self_criticism


def test_signal_guidance_is_stance_specific_and_does_not_echo_user_text():
    text = "I am a failure and the same thought keeps looping"
    guidance = build_signal_guidance(text)
    assert "do not debate the thought" in guidance.lower()
    assert "separate event, verdict, and action-demand" in guidance
    assert text not in guidance


def test_neutral_guidance_offers_permissioned_daily_summary_without_self_prompting():
    guidance = build_signal_guidance("I think I am done for today")
    assert "optional daily summary" in guidance
    assert "ask permission" in guidance
    assert "never start it" in guidance


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

    assert "menu of support options" in guidance
    assert "understanding, a tiny action, or simply company" not in guidance


def test_session_reset_erases_trajectory(tmp_path):
    record_lifeboat_trajectory(tmp_path, "session", "I feel hopeless")
    record_lifeboat_response_fingerprint(tmp_path, "session", "same reply")
    assert clear_lifeboat_trajectory(tmp_path, "session")
    state = json.loads((tmp_path / "state" / "lifeboat-psychology.json").read_text())
    assert not state.get("response_fingerprints")
    assert not clear_lifeboat_trajectory(tmp_path, "session")


def test_response_ledger_detects_only_recent_same_session_duplicates(tmp_path):
    assert not record_lifeboat_response_fingerprint(tmp_path, "session", "same reply")
    assert record_lifeboat_response_fingerprint(tmp_path, "session", "same reply")
    assert not record_lifeboat_response_fingerprint(tmp_path, "other", "same reply")
    state = json.loads((tmp_path / "state" / "lifeboat-psychology.json").read_text())
    serialized = json.dumps(state)
    assert "same reply" not in serialized
    assert len(state["response_fingerprints"]) <= 24


def test_turn_policy_adapts_to_safety_action_sharing_and_pause():
    assert select_lifeboat_turn_policy("I may hurt myself tonight").mode == "safety"
    assert select_lifeboat_turn_policy("מה לעשות עכשיו?").mode == "act-or-clarify"
    assert select_lifeboat_turn_policy("אני מרגיש שהכול כבד ואני לא יודע למה").mode == "attune"
    assert select_lifeboat_turn_policy("זה עזר לי, נעצור להיום").mode == "user-led-close"
