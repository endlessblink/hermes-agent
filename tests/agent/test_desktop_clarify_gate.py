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


def test_accepts_question_marks_that_belong_to_urls_and_code() -> None:
    """A `?` inside a link or code is punctuation, not a question to the user."""

    for response in (
        "Done. Tracking link: https://example.com/page?utm_source=hermes&utm_medium=chat",
        "I updated the parser:\n\n```python\nPATTERN = re.compile(r\"a?b\")\n```\n\nAll tests pass.",
        "Use `curl 'https://api.example.com/v1/items?limit=50'` to page through results.",
        "Removed the `foo?.bar` optional chaining and shipped it.",
    ):
        decision = evaluate_desktop_clarify_output(
            response,
            platform="desktop",
            valid_tool_names={"clarify"},
        )
        assert decision.accepted is True, response


def test_still_rejects_a_prose_question_that_sits_next_to_code() -> None:
    decision = evaluate_desktop_clarify_output(
        "Here is the snippet:\n\n```python\nurl = \"https://x.test/?a=1\"\n```\n\n"
        "Should I apply this to the other profiles too?",
        platform="desktop",
        valid_tool_names={"clarify"},
    )

    assert decision.accepted is False
    assert decision.reason == "prose_question_requires_clarify"


def test_accepts_a_checklist_phrased_as_questions() -> None:
    """The reported regression: a Hebrew checklist vanished from the chat."""

    response = """קיבלתי. הצ׳ק-ליסט נוסח כשאלות, וזה הפעיל את הבעיה.

- המסר ברור ב-3 השניות הראשונות?
- הטקסט קריא?
- התנועה משרתת את ההסבר?
- אין עומס ויזואלי?"""

    assert evaluate_desktop_clarify_output(
        response,
        platform="desktop",
        valid_tool_names={"clarify"},
    ).accepted


def test_accepts_rhetorical_and_quoted_questions() -> None:
    for response in (
        "> Should we ship this?\n\nWe already shipped it yesterday.",
        "Was the gate too strict? Yes. I narrowed it and the tests pass.",
        "1. Is the token valid?\n2. Is the session live?\n\nBoth checks now run on startup.",
    ):
        assert evaluate_desktop_clarify_output(
            response,
            platform="desktop",
            valid_tool_names={"clarify"},
        ).accepted, response


def test_still_rejects_a_closing_question_to_the_user() -> None:
    for response in (
        "I found two viable approaches. Which one do you want?",
        "סיימתי את הסקירה. באיזו גרסה להתמקד?",
        "The report is ready.\n\n- option A\n- option B\n\nWhich should I use?",
        "أنهيت المراجعة. أي خيار تفضل؟",
    ):
        decision = evaluate_desktop_clarify_output(
            response,
            platform="desktop",
            valid_tool_names={"clarify"},
        )
        assert decision.accepted is False, response
        assert decision.reason == "prose_question_requires_clarify"


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
