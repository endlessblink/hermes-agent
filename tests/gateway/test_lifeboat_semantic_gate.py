from __future__ import annotations

import json

import pytest

from gateway.lifeboat_semantic_gate import (
    SemanticVerdict,
    build_semantic_messages,
    parse_semantic_verdict,
    run_semantic_shadow,
    semantic_gate_enabled,
    semantic_shadow_enabled,
)


def verdict(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "pass": True,
        "repeated_request": False,
        "invented_user_goal": False,
        "responsibility_handoff": False,
        "unsupported_user_fact": False,
        "premature_closure": False,
        "concrete_continuation": True,
        "evidence_turn_ids": ["u3"],
        "reason": "continues the latest event",
    }
    value.update(overrides)
    return value


def test_parser_keeps_independent_failure_flags() -> None:
    parsed = parse_semantic_verdict(verdict(
        **{"pass": False, "repeated_request": True, "responsibility_handoff": True}
    ))

    assert isinstance(parsed, SemanticVerdict)
    assert parsed.failures == (
        "repeated_request",
        "responsibility_handoff",
    )
    assert parsed.evidence_turn_ids == ("u3",)


def test_parser_rejects_unconfirmed_outcome_and_premature_summary() -> None:
    parsed = parse_semantic_verdict(verdict(
        **{
            "pass": False,
            "unsupported_user_fact": True,
            "premature_closure": True,
        }
    ))

    assert parsed.failures == ("unsupported_user_fact", "premature_closure")


def test_parser_rejects_incomplete_or_non_boolean_output() -> None:
    with pytest.raises(ValueError, match="missing fields"):
        parse_semantic_verdict({"pass": True})
    with pytest.raises(ValueError, match="must be boolean"):
        parse_semantic_verdict(verdict(repeated_request="no"))
    with pytest.raises(ValueError, match="JSON"):
        parse_semantic_verdict("not json")


def test_messages_label_assistant_history_as_non_evidence() -> None:
    messages = build_semantic_messages(
        "כן",
        "אז נתחיל מאתמול.",
        recent_turns=[
            {"id": "u1", "role": "user", "content": "קיבלתי שני מספרים"},
            {"id": "a1", "role": "assistant", "content": "אולי תפנה לאחת מהן"},
        ],
        trusted_state="u1: user explicitly received two numbers",
    )

    joined = "\n".join(item["content"] for item in messages)
    assert "assistant text is not evidence" in joined
    assert "קיבלתי שני מספרים" in joined
    assert "אולי תפנה לאחת מהן" in joined
    assert "SUPPORT_SCORE" not in joined
    assert "A user action is not evidence of the user's reason" in joined
    assert "tentative read must be visibly offered as a guess" in joined
    assert "premature_closure=true" in joined
    assert "assistant question, inference, or summary does not confirm the outcome" in joined
    assert "known event already gives the assistant a reasonable next step" in joined


def test_shadow_checker_never_changes_delivery_and_records_valid_result() -> None:
    result = run_semantic_shadow(
        lambda messages: json.dumps(verdict(remark="ignored")),
        "מה קרה?",
        "נמשיך ממה שסיפרת.",
    )

    assert result.error == ""
    assert result.verdict is not None
    assert result.verdict.passed is True


def test_shadow_checker_contains_only_error_on_malformed_response() -> None:
    result = run_semantic_shadow(lambda messages: "{}", "כן", "draft")

    assert result.verdict is None
    assert result.error == "ValueError"


def test_shadow_is_opt_in_by_flag_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert semantic_shadow_enabled() is False
    (tmp_path / "lifeboat-semantic-shadow").touch()
    assert semantic_shadow_enabled() is True


def test_gate_has_a_separate_opt_in_flag(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert semantic_gate_enabled() is False
    (tmp_path / "lifeboat-semantic-gate").touch()
    assert semantic_gate_enabled() is True


def test_semantic_pass_must_agree_with_failure_flags() -> None:
    with pytest.raises(ValueError, match="must agree"):
        parse_semantic_verdict(
            {
                "pass": False,
                "repeated_request": False,
                "invented_user_goal": False,
                "responsibility_handoff": False,
                "unsupported_user_fact": False,
                "premature_closure": False,
                "concrete_continuation": True,
            }
        )
