import json

from scripts.lifeboat_live_gate import validate_live_assessment


def _case(case_type: str) -> dict:
    return {
        "case_type": case_type,
        "user_initiated": True,
        "rendered_reply_seen": True,
        "status_bubble_seen": False,
        "unsolicited_messages": 0,
        "duplicate_messages": 0,
        "response_message_count": 1,
        "human_ratings": {
            "specificity": 4,
            "naturalness": 4,
            "agency": 4,
            "open_door": 4,
            "safety": 5,
            "would_continue": True,
        },
    }


def _evidence() -> dict:
    return {
        "schema_version": 1,
        "run_id": "test-run",
        "reviewer_role": "user",
        "reviewed_at": "2026-08-11T12:00:00+03:00",
        "authenticated_runtime": {
            "telegram_connected": True,
            "gateway_pid": 123,
            "source_revision": "abc123",
        },
        "cases": [_case("ordinary_support"), _case("repair_or_distress")],
    }


def test_live_gate_accepts_two_authenticated_user_reviewed_cases():
    assert validate_live_assessment(_evidence()) == ()


def test_live_gate_rejects_missing_case_and_bad_quality():
    evidence = _evidence()
    evidence["cases"] = [_case("ordinary_support")]
    evidence["cases"][0]["status_bubble_seen"] = True
    evidence["cases"][0]["human_ratings"]["naturalness"] = 3

    failures = validate_live_assessment(evidence)

    assert "$.cases[0].status_bubble_seen" in failures
    assert "$.cases[0].human_ratings.naturalness" in failures
    assert "missing_case_type:repair_or_distress" in failures


def test_live_gate_rejects_raw_transcript_fields():
    evidence = _evidence()
    evidence["cases"][0]["response_text"] = "private reply"

    assert any(item.startswith("raw_text_field:") for item in validate_live_assessment(evidence))


def test_live_gate_rejects_unconnected_runtime():
    evidence = _evidence()
    evidence["authenticated_runtime"]["telegram_connected"] = False

    assert "telegram_not_authenticated_and_connected" in validate_live_assessment(evidence)
