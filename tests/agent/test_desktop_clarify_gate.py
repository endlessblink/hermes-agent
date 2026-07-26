from agent.desktop_clarify_gate import evaluate_desktop_clarify_output


def test_rejects_a_single_hebrew_prose_question_when_clarify_is_available() -> None:
    decision = evaluate_desktop_clarify_output(
        "לפני שאני מתקדם — מה הבעיה בשיעור הראשון?",
        platform="desktop",
        valid_tool_names={"clarify"},
    )

    assert decision.accepted is False
    assert decision.reason == "prose_question_requires_clarify"


def test_accepts_non_question_output_and_non_desktop_surfaces() -> None:
    assert evaluate_desktop_clarify_output(
        "I completed the requested review.",
        platform="desktop",
        valid_tool_names={"clarify"},
    ).accepted
    assert evaluate_desktop_clarify_output(
        "What should I do?",
        platform="telegram",
        valid_tool_names={"clarify"},
    ).accepted
    assert evaluate_desktop_clarify_output(
        "What should I do?",
        platform="desktop",
        valid_tool_names=set(),
    ).accepted


def test_accepts_the_durable_personal_assistant_interview_card() -> None:
    response = """```hermes-ui
{"type":"task-profile-review","question":{"label":"How urgent is this?"}}
```"""

    assert evaluate_desktop_clarify_output(
        response,
        platform="desktop",
        valid_tool_names={"clarify"},
        allow_personal_assistant_interview_artifact=True,
    ).accepted
