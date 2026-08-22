"""Crisis classification must read the user, not the machinery around them.

A Life-Boat message can carry quoted assistant replies, background-process
output, code and test fixtures, and memory context. None of that is evidence
about the user's current safety state, and treating it as such would fire a
crisis response at a diff.
"""

from __future__ import annotations

from gateway.lifeboat_psychology import classify_lifeboat_signals


def test_genuine_hebrew_crisis_signal_is_detected() -> None:
    assert classify_lifeboat_signals("אני כבר לא רוצה לחיות").possible_crisis is True


def test_genuine_english_crisis_signal_is_detected() -> None:
    assert classify_lifeboat_signals("i want to kill myself").possible_crisis is True


def test_hebrew_negation_is_not_a_crisis() -> None:
    assert classify_lifeboat_signals("אין לי כוונה לפגוע בעצמי").possible_crisis is False


def test_english_negation_is_not_a_crisis() -> None:
    assert classify_lifeboat_signals("I don't want to hurt myself").possible_crisis is False


def test_crisis_words_inside_a_code_fence_are_ignored() -> None:
    text = "בדקתי את הקוד:\n```\nif user_says('kill myself'):\n    escalate()\n```\nמה דעתך?"

    assert classify_lifeboat_signals(text).possible_crisis is False


def test_crisis_words_inside_a_background_process_dump_are_ignored() -> None:
    """The 17:08 dump pasted a whole diff into the topic; it is not a disclosure."""
    text = (
        "[IMPORTANT: Background process proc_68f06dca2243 finished with exit code -15~ "
        "Here's the final output:\n"
        "+def test_crisis(): assert classify('i want to kill myself').possible_crisis\n]"
    )

    assert classify_lifeboat_signals(text).possible_crisis is False


def test_quoted_assistant_reply_is_ignored() -> None:
    text = "[Replying to your previous message: אמרת לי שלא רוצה לחיות]\nרק רציתי להבהיר משהו."

    assert classify_lifeboat_signals(text).possible_crisis is False


def test_diff_lines_are_ignored() -> None:
    text = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-    # better off dead\n+    # ok\n"

    assert classify_lifeboat_signals(text).possible_crisis is False


def test_memory_context_block_is_ignored() -> None:
    text = "<memory-context>המשתמש אמר בעבר שלא רוצה לחיות</memory-context>\nמה שלומך היום?"

    assert classify_lifeboat_signals(text).possible_crisis is False


def test_a_real_disclosure_beside_quoted_material_still_counts() -> None:
    text = "```\nsome code\n```\nאני באמת לא רוצה לחיות יותר."

    assert classify_lifeboat_signals(text).possible_crisis is True


def test_bare_background_process_dump_is_ignored() -> None:
    """The real 17:08 dump carried no "IMPORTANT:" prefix, only the brackets."""
    text = (
        "[Background process proc_68f06dca2243 finished with exit code -15~ "
        "Here's the final output:\n"
        "+    assert classify('i want to kill myself').possible_crisis\n]"
    )

    assert classify_lifeboat_signals(text).possible_crisis is False


def test_bare_still_running_dump_is_ignored() -> None:
    text = "[Background process proc_abc is still running~ partial output: better off dead]"

    assert classify_lifeboat_signals(text).possible_crisis is False
