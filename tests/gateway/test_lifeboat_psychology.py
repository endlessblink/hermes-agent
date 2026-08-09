from gateway.lifeboat_psychology import (
    LifeBoatSignals,
    build_signal_guidance,
    classify_lifeboat_signals,
)


def test_classifies_current_turn_signals_without_diagnosis():
    assert classify_lifeboat_signals(
        "I keep thinking I am a failure and cannot stop overthinking"
    ) == LifeBoatSignals(thought_loop=True, self_criticism=True)
    assert classify_lifeboat_signals("אין לי כוח להמשיך") .possible_crisis
    assert classify_lifeboat_signals("I feel hopeless and empty").depressive_thoughts


def test_signal_guidance_is_stance_specific_and_does_not_echo_user_text():
    text = "I am a failure and the same thought keeps looping"
    guidance = build_signal_guidance(text)
    assert "do not debate the thought" in guidance.lower()
    assert "Separate the person's identity" in guidance
    assert text not in guidance


def test_possible_crisis_prioritizes_direct_safety_and_human_support():
    guidance = build_signal_guidance("I want to kill myself")
    assert "immediate danger" in guidance
    assert "local emergency/crisis support" in guidance
    assert "abstract coaching alone" in guidance
