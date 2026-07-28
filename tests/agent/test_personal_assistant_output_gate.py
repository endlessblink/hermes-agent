import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from agent.personal_assistant_output_gate import (
    build_grounded_plan_fallback,
    build_safe_interview_fallback,
    explicit_durable_update_requested,
    explicit_task_fact_update_requested,
    evaluate_personal_assistant_output,
    extract_personal_assistant_recommendations,
    should_build_grounded_plan_fallback,
)


def test_grounded_plan_fallback_excludes_completed_tasks_and_stays_compact():
    records = {
        "done": {"id": "done", "title": "בדיקת צילום הקרקפת", "status": "done", "priority": "high"},
        "lotem": {"id": "lotem", "title": "לבדוק שוב עם לוטם", "status": "todo", "priority": "high", "dueDate": "2026-07-22"},
        "insurance": {"id": "insurance", "title": "לברר החזר ביטוח", "status": "todo", "priority": "high", "dueDate": "2026-07-23"},
        "course": {"id": "course", "title": "להכין שיעור לקורס", "status": "todo", "priority": "medium", "dueDate": "2026-07-25"},
        "later": {"id": "later", "title": "לסדר ארכיון", "status": "todo", "priority": "low", "dueDate": "2026-08-01"},
    }

    fallback = build_grounded_plan_fallback(
        task_inventory_records=records,
        task_details={key: value for key, value in records.items() if key != "later"},
        user_message="תכנן מחדש את שאר היום ותן לי 3 אפשרויות",
    )
    artifact = json.loads(fallback.removeprefix("```hermes-ui\n").removesuffix("\n```"))

    assert artifact["type"] == "task-table"
    assert [row["id"] for row in artifact["rows"]] == ["lotem", "insurance", "course"]
    assert all(len(row["actions"]) == 1 for row in artifact["rows"])
    assert [action["label"] for action in artifact["actions"]] == [
        "שנה זמן או אנרגיה",
        "הצג אפשרויות אחרות",
    ]
    assert "לא ידוע" not in fallback
    assert "בדיקת צילום הקרקפת" not in fallback


def test_grounded_plan_fallback_uses_human_due_labels_instead_of_iso_dates() -> None:
    today = datetime.now(ZoneInfo("Asia/Jerusalem")).date()
    records = {
        key: {
            "id": key,
            "title": title,
            "status": "todo",
            "priority": "high",
            "dueDate": due.isoformat(),
        }
        for key, title, due in (
            ("today", "משימה להיום", today),
            ("tomorrow", "משימה למחר", today + timedelta(days=1)),
            ("later", "משימה להמשך", today + timedelta(days=4)),
        )
    }

    fallback = build_grounded_plan_fallback(
        task_inventory_records=records,
        task_details=records,
        user_message="תכנן לי את מחר",
    )

    assert today.isoformat() not in fallback
    assert (today + timedelta(days=1)).isoformat() not in fallback
    assert "להיום" in fallback
    assert "למחר" in fallback


def test_grounded_fallback_ranks_complete_inventory_not_recent_detail_reads() -> None:
    records = {
        "medium-detail": {
            "id": "medium-detail",
            "title": "משימה בינונית שנקראה",
            "status": "todo",
            "priority": "medium",
            "dueDate": "2026-07-23",
        },
        "high-inventory": {
            "id": "high-inventory",
            "title": "משימה חשובה מכלל המלאי",
            "status": "todo",
            "priority": "high",
            "dueDate": "2026-07-23",
        },
        "high-second": {
            "id": "high-second",
            "title": "משימה חשובה נוספת",
            "status": "todo",
            "priority": "high",
            "dueDate": "2026-07-24",
        },
    }

    fallback = build_grounded_plan_fallback(
        task_inventory_records=records,
        task_details={"medium-detail": records["medium-detail"]},
        user_message="תכנן לי את מחר",
    )
    artifact = json.loads(fallback.removeprefix("```hermes-ui\n").removesuffix("\n```"))

    assert artifact["rows"][0]["id"] == "high-inventory"


def test_grounded_fallback_uses_all_sources_and_avoids_equal_recent_repeats() -> None:
    flowstate = {
        "recent": {
            "id": "recent",
            "sourceId": "flowstate",
            "title": "משימה שכבר הוצעה",
            "status": "todo",
            "priority": "high",
            "dueDate": "2026-07-24",
        },
        "fresh-flowstate": {
            "id": "fresh-flowstate",
            "sourceId": "flowstate",
            "title": "משימה חדשה ב־FlowState",
            "status": "todo",
            "priority": "high",
            "dueDate": "2026-07-24",
        },
    }
    notion = {
        "notion-one": {
            "id": "notion-one",
            "sourceId": "notion-bina-work",
            "title": "משימה חשובה מ־Notion",
            "status": "open",
            "priority": "high",
            "dueDate": "2026-07-24",
        },
        "notion-two": {
            "id": "notion-two",
            "sourceId": "notion-bina-work",
            "title": "משימה נוספת מ־Notion",
            "status": "open",
            "priority": "medium",
            "dueDate": "2026-07-25",
        },
    }

    fallback = build_grounded_plan_fallback(
        task_inventory_records=flowstate,
        task_details={},
        candidate_records={
            "flowstate": flowstate,
            "notion-bina-work": notion,
        },
        recent_recommendations=[{"taskId": "recent"}],
        calendar_receipt={"coverage": {"calendarCount": 6}},
        availability="09:00-21:00",
        planning_date="2026-07-24",
        user_message="תכנן לי את מחר",
    )
    artifact = json.loads(fallback.removeprefix("```hermes-ui\n").removesuffix("\n```"))
    selected = [row["id"] for row in artifact["rows"]]

    assert "notion-one" in selected
    assert "fresh-flowstate" in selected
    assert selected.index("fresh-flowstate") < selected.index("recent")
    assert "FlowState" not in artifact["rows"][selected.index("notion-one")]["cells"]["reason"]
    assert artifact["description"] == (
        "נבדקו 4 משימות מכל 2 מקורות · 6 יומנים · זמינות 09:00-21:00."
    )


def test_grounded_fallback_is_immediate_only_after_complete_coverage_and_task_reads() -> None:
    assert should_build_grounded_plan_fallback(
        reason="priority_ranking_required",
        task_inventory_complete=True,
        task_details_count=3,
    )
    assert not should_build_grounded_plan_fallback(
        reason="configured_task_source_coverage_incomplete",
        task_inventory_complete=True,
        task_details_count=3,
    )
    assert not should_build_grounded_plan_fallback(
        reason="task_duration_fidelity_required",
        task_inventory_complete=False,
        task_details_count=3,
    )
    assert not should_build_grounded_plan_fallback(
        reason="task_duration_fidelity_required",
        task_inventory_complete=True,
        task_details_count=2,
    )


def test_planning_output_never_exposes_internal_coverage_jargon() -> None:
    for response in (
        "מגבלת כיסוי: 10 פריטי הגנה לא נבדקו מחדש.",
        "יש עדיין כמה פריטי רקע שלא נכנסו לעדכון ההגנה הנוכחי.",
    ):
        decision = evaluate_personal_assistant_output(
            response,
            interview=None,
            intent_action="planning.query",
            user_message="תכנן לי את שאר היום",
        )

        assert decision.reason == "internal_coverage_jargon_exposed"


def _fence(payload: dict) -> str:
    return f"```hermes-ui\n{json.dumps(payload)}\n```"


def _active_interview(*, readiness_approved: bool = False) -> dict:
    return {
        "interviewId": "planning-1",
        "interviewRevision": 4,
        "status": "active",
        "readinessApproved": readiness_approved,
        "currentTaskId": "pet-results",
        "currentQuestionId": "pet-next-step",
    }


def _fresh_calendar_receipt() -> dict:
    now = datetime.now(timezone.utc)
    return {
        "status": "complete",
        "complete": True,
        "expiresAt": (now + timedelta(minutes=5)).isoformat(),
        "timezone": "Asia/Jerusalem",
        "range": {"startDate": "2026-07-21", "endDate": "2026-07-22"},
    }


def test_exhausted_planning_without_an_interview_does_not_claim_one_can_be_resumed() -> None:
    fallback = build_safe_interview_fallback(None)

    assert "resume the interview" not in fallback
    assert "could not finish checking the planning inputs" in fallback
    assert "no plan was applied" in fallback


def test_daily_grounding_fallback_asks_one_short_hebrew_day_question() -> None:
    fallback = build_safe_interview_fallback(
        {
            "interviewId": "today",
            "interviewRevision": 1,
            "mode": "daily-grounding",
            "tasks": [
                {"taskId": "day-context", "title": "תכנון שאר היום", "profile": {}}
            ],
            "cursor": {"taskId": "day-context", "questionId": "energy"},
        }
    )

    assert "כמה אנרגיה יש לך להמשך היום?" in fallback
    assert "נמוכה" in fallback
    assert '"progress":{"current":1,"total":4}' in fallback
    assert "How urgent" not in fallback


def test_progress_review_fallback_asks_what_was_completed_before_recommending() -> None:
    interview = {
        "interviewId": "today",
        "interviewRevision": 2,
        "mode": "daily-grounding",
        "questionOrder": ["progressReview"],
        "tasks": [
            {"taskId": "day-context", "title": "תכנון שאר היום", "profile": {}}
        ],
        "cursor": {
            "taskId": "day-context",
            "questionId": "progressReview",
        },
    }

    fallback = build_safe_interview_fallback(interview)

    assert "מה כבר הושלם מאז הבדיקה האחרונה?" in fallback
    assert "שמות משימות" in fallback


def test_future_day_fallback_asks_one_availability_question() -> None:
    fallback = build_safe_interview_fallback(
        {
            "interviewId": "tomorrow",
            "interviewRevision": 1,
            "mode": "daily-grounding",
            "planningDate": "2099-01-02",
            "questionOrder": ["availability"],
            "tasks": [{"taskId": "day-context", "title": "תכנון מחר", "profile": {}}],
            "cursor": {"taskId": "day-context", "questionId": "availability"},
        }
    )
    assert "מעבר להתחייבויות שכבר ביומן" in fallback
    assert "בלי התחייבויות" not in fallback
    assert '"progress":{"current":1,"total":1}' in fallback
    assert "כמה אנרגיה יש לך להמשך היום" not in fallback


def test_completed_daily_grounding_fallback_does_not_reopen_an_empty_interview() -> None:
    fallback = build_safe_interview_fallback(
        {
            "interviewId": "today",
            "interviewRevision": 5,
            "status": "complete",
            "mode": "daily-grounding",
            "readinessApproved": True,
            "tasks": [
                {
                    "taskId": "day-context",
                    "title": "תכנון שאר היום",
                    "profile": {
                        "energy": "medium",
                        "workBoundary": "20:00",
                        "hardCommitments": "none",
                        "location": "home",
                    },
                }
            ],
            "cursor": {"taskId": "", "questionId": ""},
        }
    )

    assert "```hermes-ui" not in fallback
    assert "could not finish checking the planning inputs" in fallback
    assert "no plan was applied" in fallback


def test_rejects_day_timeline_fields_the_desktop_cannot_render() -> None:
    decision = evaluate_personal_assistant_output(
        _fence(
            {
                "type": "day-timeline",
                "date": "2026-07-22",
                "blocks": [
                    {
                        "id": "blood-tests",
                        "label": "לבקש בדיקות דם",
                        "taskId": "task-blood-tests",
                        "kind": "focus",
                        "note": "unsupported here",
                    }
                ],
            }
        ),
        interview=_active_interview(readiness_approved=True),
        intent_action="workflow.advance",
    )

    assert decision.accepted is False
    assert decision.reason == "invalid_day_timeline_contract"
    assert "note" in decision.retry_instruction


def test_rejects_day_timeline_with_more_blocks_than_desktop_can_render() -> None:
    decision = evaluate_personal_assistant_output(
        _fence(
            {
                "type": "day-timeline",
                "date": "2026-07-22",
                "blocks": [
                    {
                        "id": f"block-{index}",
                        "label": f"Task {index}",
                        "startTime": f"{index:02d}:00",
                        "endTime": f"{index:02d}:30",
                    }
                    for index in range(13)
                ],
            }
        ),
        interview=_active_interview(readiness_approved=True),
        intent_action="workflow.advance",
    )

    assert decision.accepted is False
    assert decision.reason == "invalid_day_timeline_contract"
    assert "at most 12 blocks" in decision.retry_instruction


def test_rejects_day_timeline_block_values_the_desktop_cannot_render() -> None:
    for field, value in (("status", "pending"), ("kind", "deep-work"), ("status", None), ("kind", None)):
        decision = evaluate_personal_assistant_output(
            _fence(
                {
                    "type": "day-timeline",
                    "date": "2026-07-22",
                    "blocks": [{"id": "block-1", "label": "Task", field: value}],
                }
            ),
            interview=_active_interview(readiness_approved=True),
            intent_action="workflow.advance",
        )

        assert decision.accepted is False
        assert decision.reason == "invalid_day_timeline_contract"
        assert field in decision.retry_instruction


def test_current_daily_grounding_cannot_be_bypassed_by_a_stale_calendar_receipt() -> None:
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo("Asia/Jerusalem")).date().isoformat()
    interview = {
        "interviewId": "today",
        "interviewRevision": 1,
        "status": "active",
        "mode": "daily-grounding",
        "planningDate": today,
        "readinessApproved": False,
        "tasks": [{"taskId": "day-context", "title": "תכנון שאר היום", "profile": {}}],
        "cursor": {"taskId": "day-context", "questionId": "energy"},
    }
    stale_receipt = _fresh_calendar_receipt()
    stale_receipt["range"] = {"startDate": "2026-07-20", "endDate": "2026-07-21"}

    decision = evaluate_personal_assistant_output(
        "לא ניתן לתכנן כרגע.",
        interview=interview,
        intent_action="planning.query",
        calendar_receipt=stale_receipt,
        planning_interview_required=True,
    )

    assert decision.accepted is False
    assert decision.reason == "planning_interview_required"


def test_planning_query_cannot_finish_with_a_calendar_status_instead_of_recommendations() -> None:
    decision = evaluate_personal_assistant_output(
        "בדיקת היומן הושלמה בהצלחה: נמצאו 4 אירועים.",
        interview=None,
        intent_action="planning.query",
        calendar_receipt=_fresh_calendar_receipt(),
    )

    assert decision.accepted is False
    assert decision.reason == "planning_recommendations_required"


def test_current_day_plan_requires_a_durable_interview_before_recommendations() -> None:
    decision = evaluate_personal_assistant_output(
        "Here are three options.",
        interview=None,
        intent_action="planning.query",
        user_message="תכנן לי את שאר היום",
        calendar_receipt=_fresh_calendar_receipt(),
        planning_interview_required=True,
    )

    assert decision.accepted is False
    assert decision.reason == "planning_interview_required"
    assert "personal_assistant_interview_start" in decision.retry_instruction
    assert "exactly one" in decision.retry_instruction


def test_prior_day_interview_cannot_unlock_a_current_day_plan_when_interview_is_required() -> None:
    interview = _active_interview(readiness_approved=True)
    interview["planningDate"] = "2026-07-20"

    decision = evaluate_personal_assistant_output(
        "Here are three options.",
        interview=interview,
        intent_action="planning.query",
        user_message="תכנן לי את שאר היום",
        calendar_receipt=_fresh_calendar_receipt(),
        planning_interview_required=True,
    )

    assert decision.accepted is False
    assert decision.reason == "planning_interview_required"


def test_same_day_interview_accepts_timestamp_shaped_planning_dates() -> None:
    response = _fence(
        {
            "type": "task-profile-review",
            "id": "pet-next-step-card",
            "interviewId": "planning-1",
            "revision": 4,
            "title": "PET results",
            "task": {"id": "pet-results", "title": "PET results"},
            "progress": {"current": 1, "total": 6},
            "profileFields": [{"id": "pet-next-step", "label": "Next check"}],
            "question": {
                "id": "pet-next-step",
                "profileFieldId": "pet-next-step",
                "label": "What is the next concrete check?",
                "type": "single-choice",
                "options": [{"value": "check", "label": "Check results"}],
                "allowCustomAnswer": True,
            },
        }
    )
    interviews = []
    planning_date_interview = _active_interview()
    planning_date_interview["planningDate"] = "2026-07-21T09:00:00+03:00"
    interviews.append(planning_date_interview)
    source_snapshot_interview = _active_interview()
    source_snapshot_interview["sourceSnapshot"] = {
        "localDate": "2026-07-21T09:00:00+03:00"
    }
    interviews.append(source_snapshot_interview)

    for interview in interviews:
        decision = evaluate_personal_assistant_output(
            response,
            interview=interview,
            intent_action="planning.query",
            user_message="תכנן לי את שאר היום",
            calendar_receipt=_fresh_calendar_receipt(),
            planning_interview_required=True,
        )

        assert decision.accepted is True


def test_planning_query_rejects_coverage_that_omits_a_configured_task_source() -> None:
    decision = evaluate_personal_assistant_output(
        "Here are three options.",
        interview=None,
        intent_action="planning.query",
        calendar_receipt=_fresh_calendar_receipt(),
        expected_task_source_ids=frozenset({"alpha", "beta"}),
        coverage_recorded=True,
        coverage_receipt={
            "sources": [{"id": "alpha", "status": "fresh", "revision": "1"}],
        },
    )

    assert decision.accepted is False
    assert decision.reason == "configured_task_source_coverage_required"
    assert "beta" in decision.retry_instruction


def test_ready_daily_interview_still_requires_a_fresh_calendar_receipt() -> None:
    interview = _active_interview(readiness_approved=True)
    interview["planningDate"] = "2026-07-21"
    expired = _fresh_calendar_receipt()
    expired["expiresAt"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

    decision = evaluate_personal_assistant_output(
        "Here are three options.",
        interview=interview,
        intent_action="planning.query",
        calendar_receipt=expired,
        planning_interview_required=True,
    )

    assert decision.accepted is False
    assert decision.reason == "calendar_preflight_required"


def test_plan_rejects_partial_or_unavailable_configured_sources() -> None:
    decision = evaluate_personal_assistant_output(
        "Here are three options.",
        interview=None,
        intent_action="planning.query",
        calendar_receipt=_fresh_calendar_receipt(),
        expected_task_source_ids=frozenset({"flowstate", "notion"}),
        coverage_recorded=True,
        coverage_receipt={
            "sources": [
                {"id": "flowstate", "status": "fresh", "revision": "1"},
                {"id": "notion", "status": "partial", "revision": "first-page"},
            ],
        },
    )

    assert decision.accepted is False
    assert decision.reason == "configured_task_source_coverage_incomplete"
    assert "notion" in decision.retry_instruction


def test_plan_rejects_fresh_sources_when_protected_items_were_not_reviewed() -> None:
    decision = evaluate_personal_assistant_output(
        "Here are three options.",
        interview=None,
        intent_action="planning.query",
        calendar_receipt=_fresh_calendar_receipt(),
        expected_task_source_ids=frozenset({"flowstate", "notion"}),
        coverage_recorded=True,
        coverage_receipt={
            "complete": False,
            "missingItemIds": ["blood-tests", "insurance-refund"],
            "sources": [
                {"id": "flowstate", "status": "fresh", "revision": "1"},
                {"id": "notion", "status": "fresh", "revision": "2"},
            ],
        },
    )

    assert decision.accepted is False
    assert decision.reason == "protected_item_review_required"
    assert "flowstate_get_task" in decision.retry_instruction
    assert "blood-tests" in decision.retry_instruction


def test_planning_query_requires_three_real_task_identities() -> None:
    response = _fence(
        {
            "type": "task-table",
            "rows": [
                {"id": "one", "title": "One"},
                {"id": "two", "title": "Two"},
            ],
        }
    )

    decision = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        calendar_receipt=_fresh_calendar_receipt(),
    )

    assert decision.accepted is False
    assert decision.reason == "planning_recommendations_required"


def test_task_table_rejects_a_duplicate_task_cell_before_desktop_rendering() -> None:
    response = _fence(
        {
            "type": "task-table",
            "columns": [{"id": "task", "label": "Task"}],
            "rows": [
                {"id": "one", "title": "One", "cells": {"task": "Different"}},
                {"id": "two", "title": "Two"},
                {"id": "three", "title": "Three"},
            ],
        }
    )

    decision = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        calendar_receipt=_fresh_calendar_receipt(),
    )

    assert decision.accepted is False
    assert decision.reason == "invalid_task_table"


def test_yesterdays_interview_does_not_block_todays_three_option_shortlist() -> None:
    interview = _active_interview()
    interview["planningDate"] = "2026-07-20"
    response = _fence(
        {
            "type": "task-table",
            "rows": [
                {"id": "one", "title": "One"},
                {"id": "two", "title": "Two"},
                {"id": "three", "title": "Three"},
            ],
        }
    )

    decision = evaluate_personal_assistant_output(
        response,
        interview=interview,
        intent_action="planning.query",
        calendar_receipt=_fresh_calendar_receipt(),
    )

    assert decision.accepted is True


def test_task_shortlist_requires_a_whole_plan_adjustment_action() -> None:
    titles = {"one": "One", "two": "Two", "three": "Three"}
    response = _fence(
        {
            "type": "task-table",
            "rows": [
                {
                    "id": task_id,
                    "title": title,
                    "actions": [
                        {
                            "id": "include",
                            "label": "Include",
                            "submitText": f"Include {title} in the day plan",
                        }
                    ],
                }
                for task_id, title in titles.items()
            ],
        }
    )
    details = {
        task_id: {"id": task_id, "title": title, "instances": []}
        for task_id, title in titles.items()
    }

    decision = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset(details),
        task_details=details,
    )

    assert decision.accepted is False
    assert decision.reason == "planning_adjustment_required"


def test_daily_shortlist_rejects_dense_inventory_style_columns() -> None:
    titles = {"one": "One", "two": "Two", "three": "Three"}
    response = _fence(
        {
            "type": "task-table",
            "columns": [
                {"key": "priority", "label": "Priority"},
                {"key": "basis", "label": "Basis"},
                {"key": "window", "label": "Window"},
                {"key": "constraint", "label": "Constraint"},
            ],
            "actions": [{"id": "adjust", "label": "Adjust plan", "submitText": "Adjust the whole day plan"}],
            "rows": [
                {
                    "id": task_id,
                    "title": title,
                    "cells": {"priority": "1", "basis": "due", "window": "30m", "constraint": "none"},
                    "actions": [{"id": "include", "label": "Include", "submitText": f"Include {title} in the day plan"}],
                }
                for task_id, title in titles.items()
            ],
        }
    )
    details = {task_id: {"id": task_id, "title": title, "instances": []} for task_id, title in titles.items()}

    decision = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        user_message="תכנן לי את היום",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset(details),
        task_details=details,
    )

    assert decision.accepted is False
    assert decision.reason == "compact_daily_plan_required"


def test_daily_shortlist_requires_a_real_whole_day_adjustment_action() -> None:
    titles = {"one": "One", "two": "Two", "three": "Three"}
    response = _fence(
        {
            "type": "task-table",
            "actions": [{"id": "start", "label": "Start", "submitText": "Start the first task"}],
            "rows": [
                {
                    "id": task_id,
                    "title": title,
                    "actions": [{"id": "include", "label": "Include", "submitText": f"Include {title} in the day plan"}],
                }
                for task_id, title in titles.items()
            ],
        }
    )
    details = {task_id: {"id": task_id, "title": title, "instances": []} for task_id, title in titles.items()}

    decision = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        user_message="תכנן לי את היום",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset(details),
        task_details=details,
    )

    assert decision.accepted is False
    assert decision.reason == "planning_adjustment_required"


def test_planning_query_rejects_history_only_or_invented_task_ids() -> None:
    response = _fence(
        {
            "type": "task-table",
            "rows": [
                {"id": "real-one", "title": "One"},
                {"id": "real-two", "title": "Two"},
                {"id": "invented-rest", "title": "Rest tonight"},
            ],
        }
    )

    missing = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=False,
        task_inventory_ids=frozenset(),
    )
    assert missing.reason == "task_inventory_required"

    invented = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset({"real-one", "real-two", "real-three"}),
    )
    assert invented.reason == "invented_task_recommendation"


def test_planning_query_accepts_verified_notion_task_ids() -> None:
    response = _fence(
        {
            "type": "task-table",
            "rows": [
                {"id": "flowstate-one", "title": "One"},
                {"id": "flowstate-two", "title": "Two"},
                {"id": "notion-one", "title": "Notion task"},
            ],
        }
    )

    decision = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset({"flowstate-one", "flowstate-two"}),
        coverage_receipt={
            "complete": True,
            "reviewedItemIds": ["flowstate-one", "flowstate-two", "notion-one"],
            "missingItemIds": [],
        },
    )

    assert decision.accepted is True


def test_planning_query_rejects_focus_blocks_without_canonical_task_ids() -> None:
    response = _fence(
        {
            "type": "day-timeline",
            "date": "2026-07-21",
            "blocks": [
                {
                    "id": "invented-display-id",
                    "label": "Real task one",
                    "kind": "focus",
                    "startTime": "15:00",
                    "endTime": "16:00",
                },
                {
                    "id": "break",
                    "label": "Break",
                    "kind": "break",
                    "startTime": "16:00",
                    "endTime": "16:30",
                },
            ],
        }
    )

    decision = evaluate_personal_assistant_output(
        response,
        interview=_active_interview(readiness_approved=True),
        intent_action="workflow.advance",
        user_message="Continue personal-assistant interview planning-1 after committed answer",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset({"task-one"}),
        task_details={"task-one": {"id": "task-one", "title": "Real task one", "instances": []}},
    )

    assert decision.accepted is False
    assert decision.reason == "canonical_task_reference_required"


def test_remaining_today_plan_rejects_three_tomorrow_only_recommendations() -> None:
    response = _fence(
        {
            "type": "task-table",
            "rows": [
                {"id": "one", "title": "One", "cells": {"timing": "מחר בבוקר"}},
                {"id": "two", "title": "Two", "cells": {"timing": "מחר"}},
                {"id": "three", "title": "Three", "cells": {"timing": "tomorrow afternoon"}},
            ],
        }
    )

    decision = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        user_message="תכנן לי את שאר היום ותן לי 3 אפשרויות",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset({"one", "two", "three"}),
    )

    assert decision.accepted is False
    assert decision.reason == "remaining_today_fit_required"


def test_remaining_today_plan_must_account_for_an_event_in_progress() -> None:
    receipt = _fresh_calendar_receipt()
    now = datetime(2026, 7, 21, 9, 0, tzinfo=timezone.utc)
    receipt["capturedAt"] = now.isoformat()
    receipt["events"] = [
        {
            "id": "calendar-current",
            "summary": "Current meeting",
            "start": {"dateTime": (now - timedelta(minutes=20)).isoformat()},
            "end": {"dateTime": (now + timedelta(hours=2)).isoformat()},
        }
    ]
    response = _fence(
        {
            "type": "task-table",
            "rows": [
                {"id": "one", "title": "One", "cells": {"timing": "עכשיו"}},
                {"id": "two", "title": "Two", "cells": {"timing": "אם נשאר זמן"}},
                {"id": "three", "title": "Three", "cells": {"timing": "בערב"}},
            ],
        }
    )

    missing_event = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        user_message="תכנן לי את שאר היום",
        calendar_receipt=receipt,
        task_inventory_complete=True,
        task_inventory_ids=frozenset({"one", "two", "three"}),
    )
    assert missing_event.reason == "remaining_today_calendar_conflict_required"

    with_event = response.replace('"id": "one", "title": "One"', '"id": "calendar-current", "title": "Current meeting"')
    accepted = evaluate_personal_assistant_output(
        with_event,
        interview=None,
        intent_action="planning.query",
        user_message="תכנן לי את שאר היום",
        calendar_receipt=receipt,
        task_inventory_complete=True,
        task_inventory_ids=frozenset({"two", "three"}),
    )
    assert accepted.accepted is True


def test_remaining_today_plan_rejects_a_window_that_already_ended() -> None:
    receipt = _fresh_calendar_receipt()
    receipt["capturedAt"] = "2026-07-21T20:34:36+00:00"
    receipt["events"] = []
    response = _fence(
        {
            "type": "task-table",
            "rows": [
                {"id": "one", "title": "One", "cells": {"window": "עד 21:30 בלבד"}},
                {"id": "two", "title": "Two", "cells": {"window": "עכשיו"}},
                {"id": "three", "title": "Three", "cells": {"window": "מחר"}},
            ],
        }
    )

    decision = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        user_message="תכנן לי את שאר היום",
        calendar_receipt=receipt,
        task_inventory_complete=True,
        task_inventory_ids=frozenset({"one", "two", "three"}),
    )

    assert decision.accepted is False
    assert decision.reason == "remaining_today_window_elapsed"


def test_remaining_today_allows_one_honest_option_when_event_occupies_rest_of_day() -> None:
    receipt = _fresh_calendar_receipt()
    receipt["capturedAt"] = "2026-07-21T20:40:00+00:00"
    receipt["events"] = [
        {
            "id": "calendar-current",
            "summary": "Current meeting",
            "start": {"dateTime": "2026-07-21T21:30:00+03:00"},
            "end": {"dateTime": "2026-07-22T00:30:00+03:00"},
        }
    ]
    response = _fence(
        {
            "type": "task-table",
            "rows": [
                {
                    "id": "calendar-current",
                    "title": "Current meeting",
                    "cells": {"window": "21:30–00:30", "reason": "תופס את כל שאר היום"},
                }
            ],
        }
    )

    decision = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        user_message="תכנן לי את שאר היום ותן לי 3 אפשרויות",
        calendar_receipt=receipt,
        task_inventory_complete=True,
        task_inventory_ids=frozenset(),
    )

    assert decision.accepted is True


def test_tomorrow_plan_accepts_a_fixed_event_from_the_calendar_receipt() -> None:
    receipt = _fresh_calendar_receipt()
    receipt["range"] = {"startDate": "2026-07-23", "endDate": "2026-07-24"}
    receipt["events"] = [
        {
            "id": "calendar-tomorrow",
            "summary": "נפגש עם מוטי",
            "start": {"dateTime": "2026-07-23T20:00:00+03:00"},
            "end": {"dateTime": "2026-07-23T22:15:00+03:00"},
        }
    ]
    response = _fence(
        {
            "type": "task-table",
            "rows": [
                {"id": "one", "title": "One"},
                {"id": "two", "title": "Two"},
                {"id": "calendar-tomorrow", "title": "נפגש עם מוטי"},
            ],
        }
    )

    decision = evaluate_personal_assistant_output(
        response,
        interview={
            "status": "active",
            "planningDate": "2026-07-23",
            "readinessApproved": True,
        },
        intent_action="planning.query",
        user_message="תכנן לי את מחר",
        calendar_receipt=receipt,
        task_inventory_complete=True,
        task_inventory_ids=frozenset({"one", "two"}),
    )

    assert decision.accepted is True


def test_remaining_today_rejects_extra_options_when_event_occupies_rest_of_day() -> None:
    receipt = _fresh_calendar_receipt()
    receipt["capturedAt"] = "2026-07-21T20:40:00+00:00"
    receipt["events"] = [
        {
            "id": "calendar-current",
            "summary": "Current meeting",
            "start": {"dateTime": "2026-07-21T21:30:00+03:00"},
            "end": {"dateTime": "2026-07-22T00:30:00+03:00"},
        }
    ]
    response = _fence(
        {
            "type": "task-table",
            "rows": [
                {"id": "calendar-current", "title": "Current meeting"},
                {"id": "one", "title": "Tomorrow one", "cells": {"window": "מחר"}},
                {"id": "two", "title": "Tomorrow two", "cells": {"window": "מחר"}},
            ],
        }
    )

    decision = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        user_message="תכנן לי את שאר היום ותן לי 3 אפשרויות",
        calendar_receipt=receipt,
        task_inventory_complete=True,
        task_inventory_ids=frozenset({"one", "two"}),
    )

    assert decision.accepted is False
    assert decision.reason == "rest_day_occupied_single_option_required"


def test_planning_recommendation_rejects_a_due_date_that_disagrees_with_inventory() -> None:
    response = _fence(
        {
            "type": "task-table",
            "rows": [
                {"id": "one", "title": "1. One", "cells": {"due": "2026-07-23"}},
                {"id": "two", "title": "2. Two", "cells": {"due": "ללא מועד מוגדר"}},
                {"id": "three", "title": "3. Three", "cells": {"due": "2026-07-25"}},
            ],
        }
    )

    decision = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset({"one", "two", "three"}),
        task_inventory_records={
            "one": {"title": "One", "dueDate": "2026-07-23"},
            "two": {"title": "Two", "dueDate": "2026-07-21"},
            "three": {"title": "Three", "dueDate": "2026-07-25"},
        },
    )

    assert decision.reason == "task_source_fidelity_required"


def test_lower_priority_overdue_task_cannot_displace_high_priority_due_task() -> None:
    records = {
        "general": {
            "id": "general",
            "title": "פיתוח כללי",
            "status": "todo",
            "priority": "medium",
            "dueDate": "2026-04-02",
            "estimatedDuration": 30,
        },
        "jobs": {
            "id": "jobs",
            "title": "להגיש משרות ל10+2",
            "status": "todo",
            "priority": "high",
            "dueDate": "2026-07-21",
            "estimatedDuration": 40,
        },
        "food": {
            "id": "food",
            "title": "לקנות לאוראו אוכל וחול",
            "status": "todo",
            "priority": "high",
            "dueDate": "2026-07-21",
            "estimatedDuration": 30,
        },
    }
    response = build_grounded_plan_fallback(
        task_inventory_records=records,
        task_details=records,
        user_message="תכנן לי את מחר",
    )
    artifact = json.loads(response.removeprefix("```hermes-ui\n").removesuffix("\n```"))
    artifact["rows"] = [
        next(row for row in artifact["rows"] if row["id"] == task_id)
        for task_id in ("general", "jobs", "food")
    ]

    decision = evaluate_personal_assistant_output(
        _fence(artifact),
        interview=None,
        intent_action="planning.query",
        user_message="תכנן לי את מחר",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset(records),
        task_inventory_records=records,
        task_details=records,
    )

    assert decision.reason == "priority_ranking_required"


def test_older_due_date_outranks_duration_bonus_at_equal_priority() -> None:
    records = {
        "due-today": {
            "id": "due-today",
            "title": "להזמין כדורים דרך הקופה",
            "status": "todo",
            "priority": "high",
            "dueDate": "2026-07-23",
        },
        "due-tomorrow": {
            "id": "due-tomorrow",
            "title": "לשטוף כלים + להוריד זבל + נקיון",
            "status": "todo",
            "priority": "high",
            "dueDate": "2026-07-24",
            "estimatedDuration": 25,
        },
        "later": {
            "id": "later",
            "title": "משימה מאוחרת",
            "status": "todo",
            "priority": "medium",
            "dueDate": "2026-07-25",
        },
    }

    response = build_grounded_plan_fallback(
        task_inventory_records=records,
        task_details=records,
        planning_date="2026-07-24",
        user_message="תכנן לי את מחר",
    )
    artifact = json.loads(response.removeprefix("```hermes-ui\n").removesuffix("\n```"))

    assert [row["id"] for row in artifact["rows"][:2]] == [
        "due-today",
        "due-tomorrow",
    ]


def test_selected_named_option_is_promoted_and_keeps_tomorrow_context() -> None:
    records = {
        "urgent": {
            "id": "urgent",
            "title": "משימה דחופה",
            "status": "todo",
            "priority": "high",
            "dueDate": "2026-07-23",
        },
        "selected": {
            "id": "selected",
            "title": "האפשרות שבחרתי",
            "status": "todo",
            "priority": "medium",
            "dueDate": "2026-07-24",
        },
        "later": {
            "id": "later",
            "title": "משימה נוספת",
            "status": "todo",
            "priority": "low",
        },
    }

    response = build_grounded_plan_fallback(
        task_inventory_records=records,
        task_details=records,
        planning_date="2099-01-02",
        preferred_task_title="האפשרות שבחרתי",
        user_message="תכנן לי את מחר",
    )
    artifact = json.loads(response.removeprefix("```hermes-ui\n").removesuffix("\n```"))

    assert artifact["rows"][0]["id"] == "selected"
    assert artifact["title"] == "תוכנית גמישה סביב האפשרות שבחרתי"
    assert "לתכנון מחר" in artifact["rows"][0]["actions"][0]["submitText"]


def test_selected_option_survives_hydrated_detail_title_drift() -> None:
    candidate_records = {
        "flowstate": {
            "selected": {
                "id": "selected",
                "title": "להעביר 200$ לאלכס",
                "status": "todo",
                "priority": "high",
            },
            "other-one": {
                "id": "other-one",
                "title": "משימה אחרת",
                "status": "todo",
                "priority": "high",
            },
            "other-two": {
                "id": "other-two",
                "title": "עוד משימה",
                "status": "todo",
                "priority": "medium",
            },
        }
    }
    task_details = {
        "selected": {
            "id": "selected",
            "title": "להעביר 200 דולר לאלכס",
            "status": "todo",
            "priority": "high",
        }
    }

    response = build_grounded_plan_fallback(
        task_inventory_records={},
        task_details=task_details,
        candidate_records=candidate_records,
        preferred_task_title="להעביר 200$ לאלכס",
        user_message="",
    )
    artifact = json.loads(response.removeprefix("```hermes-ui\n").removesuffix("\n```"))

    assert artifact["rows"][0]["id"] == "selected"
    assert artifact["title"] == "תוכנית גמישה סביב להעביר 200 דולר לאלכס"


def test_show_alternatives_action_keeps_day_scope_and_excludes_visible_titles() -> None:
    records = {
        f"task-{index}": {
            "id": f"task-{index}",
            "title": f"משימה {index}",
            "status": "todo",
            "priority": "high" if index <= 3 else "medium",
            "dueDate": "2026-07-24",
        }
        for index in range(1, 7)
    }

    first_response = build_grounded_plan_fallback(
        task_inventory_records=records,
        task_details=records,
        planning_date="2099-01-02",
        user_message="תכנן לי את מחר",
    )
    first_artifact = json.loads(
        first_response.removeprefix("```hermes-ui\n").removesuffix("\n```")
    )
    first_titles = [row["title"] for row in first_artifact["rows"]]
    alternatives_action = next(
        action
        for action in first_artifact["actions"]
        if action["id"] == "show-alternatives"
    )

    assert "לתכנון מחר" in alternatives_action["submitText"]
    assert all(f"„{title}”" in alternatives_action["submitText"] for title in first_titles)
    assert all(row["id"] not in alternatives_action["submitText"] for row in first_artifact["rows"])

    replacement_response = build_grounded_plan_fallback(
        task_inventory_records=records,
        task_details=records,
        planning_date="2099-01-02",
        excluded_task_titles=first_titles,
        user_message=alternatives_action["submitText"],
    )
    replacement_artifact = json.loads(
        replacement_response.removeprefix("```hermes-ui\n").removesuffix("\n```")
    )

    assert len(replacement_artifact["rows"]) == 3
    assert not set(first_titles) & {
        row["title"] for row in replacement_artifact["rows"]
    }


def test_known_duration_must_be_visible_in_compact_shortlist() -> None:
    records = {
        task_id: {
            "id": task_id,
            "title": title,
            "status": "todo",
            "priority": "high",
            "dueDate": "2026-07-21",
            "estimatedDuration": duration,
        }
        for task_id, title, duration in (
            ("jobs", "להגיש משרות ל10+2", 40),
            ("food", "לקנות לאוראו אוכל וחול", 30),
            ("pet", "לבדוק תוצאות PET", None),
        )
    }
    response = build_grounded_plan_fallback(
        task_inventory_records=records,
        task_details=records,
        user_message="תכנן לי את מחר",
    )
    artifact = json.loads(response.removeprefix("```hermes-ui\n").removesuffix("\n```"))
    jobs = next(row for row in artifact["rows"] if row["id"] == "jobs")
    jobs["cells"]["reason"] = "עדיפות גבוהה · מועד 2026-07-21"

    decision = evaluate_personal_assistant_output(
        _fence(artifact),
        interview=None,
        intent_action="planning.query",
        user_message="תכנן לי את מחר",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset(records),
        task_inventory_records=records,
        task_details=records,
    )

    assert decision.reason == "task_duration_fidelity_required"


def test_grounded_fallback_uses_recurring_occurrence_duration_when_task_duration_is_empty() -> None:
    records = {
        "jobs": {
            "id": "jobs",
            "title": "להגיש משרות ל10+2",
            "status": "todo",
            "priority": "high",
            "dueDate": "2026-07-21",
            "estimatedDuration": None,
        },
        "food": {
            "id": "food",
            "title": "לקנות אוכל",
            "status": "todo",
            "priority": "high",
            "dueDate": "2026-07-21",
        },
        "pet": {
            "id": "pet",
            "title": "לנקות את השואב",
            "status": "todo",
            "priority": "high",
            "dueDate": "2026-07-21",
        },
    }

    details = {
        **records,
        "jobs": {
            **records["jobs"],
            "instances": [
                {
                    "id": "jobs-next",
                    "status": "scheduled",
                    "scheduledDate": "2026-07-21",
                    "duration": 40,
                }
            ],
        },
    }
    response = build_grounded_plan_fallback(
        task_inventory_records=records,
        task_details=details,
        user_message="תכנן לי את מחר",
    )
    artifact = json.loads(response.removeprefix("```hermes-ui\n").removesuffix("\n```"))
    jobs = next(row for row in artifact["rows"] if row["id"] == "jobs")

    assert "40 דק׳" in jobs["cells"]["reason"]

    jobs["cells"]["reason"] = "עדיפות גבוהה · למחר"
    decision = evaluate_personal_assistant_output(
        _fence(artifact),
        interview=None,
        intent_action="planning.query",
        user_message="תכנן לי את מחר",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset(records),
        task_inventory_records=records,
        task_details=details,
    )

    assert decision.reason == "task_duration_fidelity_required"


def test_planning_recommendations_require_fresh_full_task_details() -> None:
    response = _fence(
        {
            "type": "task-table",
            "rows": [
                {"id": "one", "title": "One"},
                {"id": "two", "title": "Two"},
                {"id": "three", "title": "Three"},
            ],
        }
    )

    decision = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset({"one", "two", "three"}),
        task_details={"one": {"id": "one"}, "two": {"id": "two"}},
    )

    assert decision.accepted is False
    assert decision.reason == "task_details_required"
    assert "flowstate_get_task" in decision.retry_instruction


def test_today_plan_rejects_task_whose_only_active_instance_is_on_a_later_date() -> None:
    response = _fence(
        {
            "type": "task-table",
            "rows": [
                {"id": "future", "title": "Future task"},
                {"id": "today", "title": "Today task"},
                {"id": "overdue", "title": "Overdue task"},
            ],
        }
    )
    details = {
        "future": {
            "id": "future",
            "instances": [{"scheduledDate": "2026-07-23", "status": "scheduled"}],
        },
        "today": {
            "id": "today",
            "instances": [{"scheduledDate": "2026-07-21", "status": "scheduled"}],
        },
        "overdue": {
            "id": "overdue",
            "instances": [{"scheduledDate": "2026-07-20", "status": "scheduled"}],
        },
    }

    decision = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        user_message="תכנן לי את היום",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset(details),
        task_details=details,
    )

    assert decision.accepted is False
    assert decision.reason == "task_schedule_fidelity_required"


def test_today_plan_accepts_fully_read_tasks_scheduled_today_or_earlier() -> None:
    response = _fence(
        {
            "type": "task-table",
            "actions": [
                {
                    "id": "adjust-day",
                    "label": "Adjust day",
                    "submitText": "Ask me one focused question about time, energy, order, or alternatives before adjusting this day plan",
                }
            ],
            "rows": [
                {"id": "one", "title": "One", "actions": [{"id": "start", "label": "Start One", "submitText": "Start One"}, {"id": "adjust", "label": "Adjust One", "submitText": "Adjust One"}]},
                {"id": "two", "title": "Two", "actions": [{"id": "start", "label": "Start Two", "submitText": "Start Two"}, {"id": "adjust", "label": "Adjust Two", "submitText": "Adjust Two"}]},
                {"id": "three", "title": "Three", "actions": [{"id": "start", "label": "Start Three", "submitText": "Start Three"}, {"id": "adjust", "label": "Adjust Three", "submitText": "Adjust Three"}]},
            ],
        }
    )
    details = {
        "one": {"id": "one", "title": "One", "instances": [{"scheduledDate": "2026-07-21"}]},
        "two": {"id": "two", "title": "Two", "instances": [{"scheduledDate": "2026-07-20"}]},
        "three": {"id": "three", "title": "Three", "instances": []},
    }

    decision = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        user_message="תכנן לי את היום",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset(details),
        task_details=details,
    )

    assert decision.accepted is True


def test_whole_plan_adjustment_does_not_block_a_complete_planning_request() -> None:
    details = {
        task_id: {"id": task_id, "title": title, "instances": []}
        for task_id, title in (("one", "One"), ("two", "Two"), ("three", "Three"))
    }
    response = _fence(
        {
            "type": "task-table",
            "actions": [
                {
                    "id": "adjust-day",
                    "label": "Adjust the evening",
                    "submitText": "Adjust the whole evening around my time and energy",
                }
            ],
            "rows": [
                {
                    "id": task_id,
                    "title": detail["title"],
                    "actions": [
                        {"id": "start", "label": f"Start {detail['title']}", "submitText": f"Start {detail['title']}"},
                        {"id": "adjust", "label": f"Adjust {detail['title']}", "submitText": f"Adjust {detail['title']}"},
                    ],
                }
                for task_id, detail in details.items()
            ],
        }
    )

    decision = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        user_message="תכנן לי את היום",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset(details),
        task_details=details,
    )

    assert decision.accepted is True

    corrected = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        user_message="המשימה One לוקחת 40 דקות והיא בעדיפות גבוהה. תזכור את זה ותתכנן מחדש.",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset(details),
        task_details=details,
    )

    assert corrected.accepted is True


def test_fully_read_planning_recommendations_require_direct_actions() -> None:
    response = _fence(
        {
            "type": "task-table",
            "rows": [
                {"id": "one", "title": "One"},
                {"id": "two", "title": "Two"},
                {"id": "three", "title": "Three"},
            ],
        }
    )
    details = {task_id: {"id": task_id, "instances": []} for task_id in ("one", "two", "three")}

    decision = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        user_message="תכנן לי את היום",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset(details),
        task_details=details,
    )

    assert decision.accepted is False
    assert decision.reason == "planning_actions_required"
    assert "submitText" in decision.retry_instruction


def test_planning_actions_use_task_names_and_never_expose_identifiers() -> None:
    task_ids = ("task-11111111", "task-22222222", "task-33333333")
    titles = ("Write proposal", "Review budget", "Plan workshop")
    response = _fence(
        {
            "type": "task-table",
            "rows": [
                {
                    "id": task_id,
                    "title": title,
                    "cells": {"internal": task_id},
                    "actions": [
                        {
                            "id": "plan",
                            "label": "Include in day",
                            "submitText": f"Include {task_id} in my day plan",
                        }
                    ],
                }
                for task_id, title in zip(task_ids, titles)
            ],
        }
    )
    details = {
        task_id: {"id": task_id, "title": title, "instances": []}
        for task_id, title in zip(task_ids, titles)
    }

    decision = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        user_message="תכנן לי את היום",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset(details),
        task_details=details,
    )

    assert decision.accepted is False
    assert decision.reason == "task_names_required"


def test_daily_plan_rejects_generic_numbered_buttons_and_single_path_rows() -> None:
    details = {
        "one": {"id": "one", "title": "Check the PET results", "instances": []},
        "two": {"id": "two", "title": "Prepare the workshop lesson", "instances": []},
        "three": {"id": "three", "title": "Reply about the photo shoot", "instances": []},
    }
    response = _fence(
        {
            "type": "task-table",
            "actions": [
                {
                        "id": "adjust-day",
                        "label": "Change the whole day",
                        "submitText": "Ask me one focused question about time, energy, order, or alternatives before changing the whole day",
                }
            ],
            "rows": [
                {
                    "id": task_id,
                    "title": detail["title"],
                    "actions": [
                        {
                            "id": "choose",
                            "label": f"Choose option {index}",
                            "submitText": f"Choose {detail['title']}",
                        }
                    ],
                }
                for index, (task_id, detail) in enumerate(details.items(), start=1)
            ],
        }
    )

    decision = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        user_message="תכנן לי את היום",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset(details),
        task_details=details,
    )

    assert decision.accepted is False
    assert decision.reason == "planning_interactivity_required"
    assert "task name" in decision.retry_instruction
    assert "two" in decision.retry_instruction


def test_three_day_options_rejects_a_ranked_task_table_as_three_fake_plans() -> None:
    details = {
        "one": {"id": "one", "title": "Check the PET results", "instances": []},
        "two": {"id": "two", "title": "Prepare the workshop lesson", "instances": []},
        "three": {"id": "three", "title": "Buy food for Oreo", "instances": []},
    }
    response = _fence(
        {
            "type": "task-table",
            "title": "Three options",
            "columns": [{"key": "priority", "label": "Priority"}],
            "actions": [
                {
                    "id": "adjust-day",
                    "label": "Adjust the day",
                    "submitText": "Adjust the whole day around my time and energy",
                }
            ],
            "rows": [
                {
                    "id": task_id,
                    "title": detail["title"],
                    "cells": {"priority": index},
                    "actions": [
                        {
                            "id": "start",
                            "label": f"Start {detail['title']}",
                            "submitText": f"Start {detail['title']}",
                        },
                        {
                            "id": "replace",
                            "label": f"Replace {detail['title']}",
                            "submitText": f"Replace {detail['title']} in the day plan",
                        },
                    ],
                }
                for index, (task_id, detail) in enumerate(details.items(), start=1)
            ],
        }
    )

    decision = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        user_message="תן לי 3 אפשרויות מעשיות לתכנון שאר היום",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset(details),
        task_details=details,
    )

    assert decision.accepted is False
    assert decision.reason == "compact_day_plan_options_required"
    assert "compact task-table" in decision.retry_instruction


def test_three_day_options_accepts_a_compact_plan_oriented_task_table() -> None:
    details = {
        "one": {"id": "one", "title": "Check the PET results", "instances": []},
        "two": {"id": "two", "title": "Prepare the workshop lesson", "instances": []},
        "three": {"id": "three", "title": "Buy food for Oreo", "instances": []},
    }
    response = _fence(
        {
            "type": "task-table",
            "title": "Three practical directions",
            "columns": [
                {"key": "time", "label": "Time"},
                {"key": "why", "label": "Why now"},
            ],
            "actions": [
                {
                    "id": "adjust-day",
                    "label": "Adjust the day",
                    "submitText": "Ask me exactly one focused question about time, energy, order, or alternatives before adjusting the whole day",
                }
            ],
            "rows": [
                {
                    "id": task_id,
                    "title": detail["title"],
                    "cells": {"time": "25 min", "why": "Fits the time and priorities"},
                    "actions": [
                        {
                            "id": "plan-around",
                            "label": f"Plan: {detail['title']}",
                            "submitText": f"Plan the remaining day around {detail['title']}",
                        },
                        {
                            "id": "alternative",
                            "label": f"Alternative: {detail['title']}",
                            "submitText": f"Show a different planning direction from {detail['title']}",
                        },
                    ],
                }
                for task_id, detail in details.items()
            ],
        }
    )

    decision = evaluate_personal_assistant_output(
        response,
        interview=None,
        intent_action="planning.query",
        user_message="תן לי 3 אפשרויות מעשיות לתכנון שאר היום",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset(details),
        task_details=details,
    )

    assert decision.accepted is True


def test_three_day_options_only_accepts_three_timelines_when_full_schedules_are_explicit() -> None:
    details = {
        f"task-{index}": {"id": f"task-{index}", "title": f"Real task {index}", "instances": []}
        for index in range(1, 7)
    }
    artifacts = []
    for option in range(3):
        task_ids = [f"task-{option * 2 + 1}", f"task-{option * 2 + 2}"]
        task_names = [details[task_id]["title"] for task_id in task_ids]
        artifacts.append(
            _fence(
                {
                    "type": "day-timeline",
                    "title": f"Plan {option + 1}",
                    "date": "2026-07-21",
                    "blocks": [
                        {
                            "id": f"block-{task_id}",
                            "taskId": task_id,
                            "label": details[task_id]["title"],
                            "durationMinutes": 30,
                        }
                        for task_id in task_ids
                    ],
                    "actions": [
                        {
                            "id": "choose-plan",
                            "label": f"Choose Plan {option + 1}",
                            "submitText": "Choose this plan with " + " and ".join(task_names),
                        },
                        {
                            "id": "adjust-plan",
                            "label": f"Adjust Plan {option + 1}",
                            "submitText": "Adjust this plan with " + " and ".join(task_names),
                        },
                    ],
                }
            )
        )

    compact_request = evaluate_personal_assistant_output(
        "\n".join(artifacts),
        interview=None,
        intent_action="planning.query",
        user_message="תן לי 3 אפשרויות מעשיות לתכנון שאר היום",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset(details),
        task_details=details,
    )

    assert compact_request.accepted is False
    assert compact_request.reason == "compact_day_plan_options_required"

    full_schedule_request = evaluate_personal_assistant_output(
        "\n".join(artifacts),
        interview=None,
        intent_action="planning.query",
        user_message="השווה לי 3 לוחות זמנים מלאים לתכנון שאר היום",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset(details),
        task_details=details,
    )

    assert full_schedule_request.accepted is True


def test_three_day_options_rejects_repeated_controls_inside_every_block() -> None:
    details = {
        f"task-{index}": {"id": f"task-{index}", "title": f"Real task {index}", "instances": []}
        for index in range(1, 7)
    }
    artifacts = []
    for option in range(3):
        task_ids = [f"task-{option * 2 + 1}", f"task-{option * 2 + 2}"]
        task_names = [details[task_id]["title"] for task_id in task_ids]
        artifacts.append(
            _fence(
                {
                    "type": "day-timeline",
                    "title": f"Plan {option + 1}",
                    "date": "2026-07-21",
                    "blocks": [
                        {
                            "id": f"block-{task_id}",
                            "taskId": task_id,
                            "label": details[task_id]["title"],
                            "durationMinutes": 30,
                            "actions": [
                                {
                                    "id": "start",
                                    "label": f"Start {details[task_id]['title']}",
                                    "submitText": f"Start {details[task_id]['title']}",
                                }
                            ],
                        }
                        for task_id in task_ids
                    ],
                    "actions": [
                        {"id": "choose", "label": "Choose", "submitText": "Choose " + " and ".join(task_names)},
                        {"id": "adjust", "label": "Adjust", "submitText": "Adjust " + " and ".join(task_names)},
                    ],
                }
            )
        )

    decision = evaluate_personal_assistant_output(
        "\n".join(artifacts),
        interview=None,
        intent_action="planning.query",
        user_message="תן לי 3 אפשרויות מעשיות לתכנון שאר היום",
        calendar_receipt=_fresh_calendar_receipt(),
        task_inventory_complete=True,
        task_inventory_ids=frozenset(details),
        task_details=details,
    )

    assert decision.accepted is False
    assert decision.reason == "compact_day_plan_options_required"
    assert "top-level" in decision.retry_instruction


def test_rejects_an_action_label_too_long_for_desktop_rendering() -> None:
    response = _fence(
        {
            "type": "day-timeline",
            "date": "2026-07-21",
            "blocks": [],
            "actions": [{"id": "choose", "label": "x" * 81, "submitText": "Choose this plan"}],
        }
    )

    decision = evaluate_personal_assistant_output(response, interview=None)

    assert decision.accepted is False
    assert decision.reason == "unrenderable_action_label"


def test_rejects_task_table_cell_shape_that_desktop_cannot_render() -> None:
    response = _fence(
        {
            "type": "task-table",
            "columns": ["task"],
            "rows": [{"id": "one", "title": "One", "cells": {"task": "One"}}],
        }
    )

    decision = evaluate_personal_assistant_output(response, interview=None)

    assert decision.accepted is False
    assert decision.reason == "invalid_task_table"


def test_rejects_week_plan_before_readiness_approval() -> None:
    decision = evaluate_personal_assistant_output(
        _fence({"type": "week-planner", "weekStart": "2026-07-20", "days": []}),
        interview=_active_interview(),
    )

    assert decision.accepted is False
    assert decision.reason == "planning_interview_incomplete"
    assert "one current task question" in decision.retry_instruction


def test_rejects_day_timeline_before_readiness_approval() -> None:
    decision = evaluate_personal_assistant_output(
        _fence({"type": "day-timeline", "date": "2026-07-20", "blocks": []}),
        interview=_active_interview(),
    )

    assert decision.accepted is False
    assert decision.reason == "planning_interview_incomplete"


def test_yesterdays_unfinished_interview_does_not_block_todays_plan() -> None:
    interview = _active_interview()
    interview["planningDate"] = "2026-07-20"

    decision = evaluate_personal_assistant_output(
        _fence({"type": "day-timeline", "date": "2026-07-21", "blocks": []}),
        interview=interview,
    )

    assert decision.accepted is True


def test_rejects_acknowledging_an_explicit_update_without_capture_proposal() -> None:
    decision = evaluate_personal_assistant_output(
        "Got it. I will remember that from now on.",
        interview=None,
        durable_capture_required=True,
        durable_capture_executed=False,
    )

    assert decision.accepted is False
    assert decision.reason == "durable_capture_required"
    assert "personal_assistant_propose_capture" in decision.retry_instruction


def test_rejects_claiming_a_capture_proposal_was_already_saved() -> None:
    decision = evaluate_personal_assistant_output(
        "נשמר: המשימה לוקחת 40 דקות ובעדיפות גבוהה.",
        interview=None,
        durable_capture_required=True,
        durable_capture_executed=True,
    )

    assert decision.accepted is False
    assert decision.reason == "durable_capture_falsely_claimed_applied"
    assert "proposal" in decision.retry_instruction.lower()
    assert "approval" in decision.retry_instruction.lower()


def test_accepts_an_explicit_not_saved_pending_approval_explanation() -> None:
    decision = evaluate_personal_assistant_output(
        "העדכון טרם נשמר: ההצעה ממתינה לאישור הגלוי שלך.",
        interview=None,
        durable_capture_required=True,
        durable_capture_executed=True,
    )

    assert decision.accepted is True


def test_capture_proposal_requires_visible_approval_explanation() -> None:
    decision = evaluate_personal_assistant_output(
        "הנה התכנון המעודכן.",
        interview=None,
        durable_capture_required=True,
        durable_capture_executed=True,
    )

    assert decision.accepted is False
    assert decision.reason == "durable_capture_approval_explanation_required"
    assert "awaiting" in decision.retry_instruction.lower()
    assert "approval" in decision.retry_instruction.lower()


def test_recognizes_explicit_lasting_updates_without_treating_normal_requests_as_updates() -> None:
    assert explicit_durable_update_requested("Please remember that I need short morning plans")
    assert explicit_durable_update_requested("From now on, do not suggest calls after 18:00")
    assert explicit_durable_update_requested("מעכשיו אל תציע לי פגישות בערב")
    assert explicit_durable_update_requested("תזכור את זה")
    assert explicit_durable_update_requested("תזכור אותה מעכשיו")
    assert not explicit_durable_update_requested("What should I do this morning?")
    assert not explicit_durable_update_requested("Will this task always take an hour?")


def test_named_task_duration_correction_requires_canonical_update_before_interview() -> None:
    message = (
        "המשימה להגיש משרות ל10+2 לוקחת 40 דקות, לא 25, "
        "והיא בעדיפות גבוהה. תזכור את זה ותתכנן את מחר מחדש."
    )
    assert explicit_task_fact_update_requested(message)

    decision = evaluate_personal_assistant_output(
        "מתי האנרגיה שלך צפויה להיות טובה יותר מחר?",
        interview=None,
        user_message=message,
        task_fact_update_required=True,
        task_fact_update_executed=False,
    )

    assert decision.accepted is False
    assert decision.reason == "canonical_task_fact_update_required"
    assert "flowstate_get_task" in decision.retry_instruction
    assert "flowstate_update_task" in decision.retry_instruction
    assert "flowstate_resize_work_block" in decision.retry_instruction


def test_runtime_facts_do_not_become_a_new_task_correction() -> None:
    message = """Act as my personal assistant for this session.

My request or current intent:
Proactively assess what would be most useful to help me with now.

Available runtime facts (data, not a prescribed workflow):
{"preferences":[{"title":"המשימה להגיש משרות ל10+2 לוקחת 40 דקות, לא 25, והיא בעדיפות גבוהה. תזכור את זה"}]}
"""

    assert explicit_task_fact_update_requested(message) is False


def test_named_task_duration_correction_allows_approval_after_canonical_preview() -> None:
    decision = evaluate_personal_assistant_output(
        "העדכון ממתין לאישור שלך.",
        interview=None,
        task_fact_update_required=True,
        task_fact_update_executed=True,
    )

    assert decision.accepted is True


def test_missing_planning_capacity_must_be_one_interactive_question() -> None:
    decision = evaluate_personal_assistant_output(
        "כדי לתכנן את מחר חסר לי רק נתון הקיבולת של מחר.",
        interview=None,
        intent_action="workflow.approve",
        user_message="אשר את תיקון להגיש משרות ל10+2",
    )

    assert decision.accepted is False
    assert decision.reason == "missing_planning_input_interaction_required"


def test_extracts_recommended_task_ids_from_supported_planning_artifacts() -> None:
    response = "\n".join(
        [
            _fence(
                {
                    "type": "day-timeline",
                    "date": "2026-07-21",
                    "blocks": [
                        {"id": "block-a", "label": "Alpha", "taskId": "task-alpha"},
                        {
                            "id": "break",
                            "label": "Break",
                            "kind": "break",
                            "taskId": "synthetic-break-id",
                        },
                    ],
                }
            ),
            _fence(
                {
                    "type": "task-table",
                    "columns": ["title"],
                    "rows": [{"id": "task-beta", "title": "Beta"}],
                }
            ),
        ]
    )

    assert extract_personal_assistant_recommendations(response) == [
        {"taskId": "task-alpha", "title": "Alpha", "surface": "day-timeline"},
        {"taskId": "task-beta", "title": "Beta", "surface": "task-table"},
    ]


def test_accepts_matching_task_profile_review() -> None:
    decision = evaluate_personal_assistant_output(
        _fence(
            {
                "type": "task-profile-review",
                "id": "pet-next-step-card",
                "interviewId": "planning-1",
                "revision": 4,
                "title": "PET results",
                "task": {"id": "pet-results", "title": "PET results"},
                "progress": {"current": 1, "total": 6},
                "profileFields": [
                    {"id": "pet-next-step", "label": "Next check"}
                ],
                "question": {
                    "id": "pet-next-step",
                    "profileFieldId": "pet-next-step",
                    "label": "What is the next concrete check?",
                    "type": "single-choice",
                    "options": [{"value": "check", "label": "Check results"}],
                    "allowCustomAnswer": True,
                },
            }
        ),
        interview=_active_interview(),
    )

    assert decision.accepted is True


def test_interview_question_precedes_configured_source_coverage() -> None:
    decision = evaluate_personal_assistant_output(
        _fence(
            {
                "type": "task-profile-review",
                "id": "pet-next-step-card",
                "interviewId": "planning-1",
                "revision": 4,
                "title": "PET results",
                "task": {"id": "pet-results", "title": "PET results"},
                "progress": {"current": 1, "total": 6},
                "profileFields": [
                    {"id": "pet-next-step", "label": "Next check"}
                ],
                "question": {
                    "id": "pet-next-step",
                    "profileFieldId": "pet-next-step",
                    "label": "What is the next concrete check?",
                    "type": "single-choice",
                    "options": [{"value": "check", "label": "Check results"}],
                    "allowCustomAnswer": True,
                },
            }
        ),
        interview=_active_interview(),
        intent_action="planning.query",
        expected_task_source_ids=frozenset({"flowstate", "notion"}),
        coverage_recorded=False,
        planning_interview_required=True,
    )

    assert decision.accepted is True


def test_planning_retry_cannot_replace_active_interview_with_blocker_prose() -> None:
    decision = evaluate_personal_assistant_output(
        "The sources are blocked, so I cannot plan the rest of today.",
        interview=_active_interview(),
        intent_action="planning.query",
        user_message="plan the rest of today",
        planning_interview_required=True,
    )

    assert decision.accepted is False
    assert decision.reason == "missing_current_interaction"
    assert "task-profile-review" in decision.retry_instruction


def test_rejects_card_for_stale_or_different_interview_projection() -> None:
    decision = evaluate_personal_assistant_output(
        _fence(
            {
                "type": "task-profile-review",
                "id": "wrong",
                "interviewId": "planning-1",
                "revision": 3,
                "title": "Wrong task",
                "task": {"id": "other-task", "title": "Wrong task"},
                "progress": {"current": 1, "total": 1},
                "profileFields": [{"id": "other-question", "label": "Other"}],
                "question": {
                    "id": "other-question",
                    "profileFieldId": "other-question",
                    "label": "Other?",
                    "type": "short-text",
                },
            }
        ),
        interview=_active_interview(),
    )

    assert decision.accepted is False
    assert decision.reason == "stale_interview_projection"


def test_rejects_matching_first_question_without_a_renderable_profile_field() -> None:
    decision = evaluate_personal_assistant_output(
        _fence(
            {
                "type": "task-profile-review",
                "id": "pet-next-step-card",
                "interviewId": "planning-1",
                "revision": 4,
                "task": {"id": "pet-results", "title": "PET results"},
                "progress": {"current": 1, "total": 1},
                "profileFields": [],
                "question": {
                    "id": "pet-next-step",
                    "profileFieldId": "pet-next-step",
                    "label": "What is the next concrete check?",
                    "type": "single-choice",
                    "options": [{"value": "check", "label": "Check results"}],
                    "required": True,
                },
            }
        ),
        interview=_active_interview(),
        intent_action="workflow.advance",
    )

    assert decision.accepted is False
    assert decision.reason == "invalid_task_profile_review"
    assert "renderable" in decision.retry_instruction


def test_rejects_choice_card_that_desktop_cannot_render() -> None:
    decision = evaluate_personal_assistant_output(
        _fence(
            {
                "type": "task-profile-review",
                "interviewId": "planning-1",
                "revision": 4,
                "task": {"id": "pet-results", "title": "PET results"},
                "progress": {"current": 1, "total": 1},
                "profileFields": [
                    {
                        "id": "pet-next-step",
                        "label": "Next check",
                        "type": "single-choice",
                        "options": ["Check results"],
                    }
                ],
                "question": {
                    "id": "pet-next-step",
                    "profileFieldId": "pet-next-step",
                    "label": "What is the next concrete check?",
                    "type": "single-choice",
                    "options": ["Check results"],
                },
            }
        ),
        interview=_active_interview(),
    )

    assert decision.accepted is False
    assert decision.reason == "invalid_task_profile_review"
    assert "renderable" in decision.retry_instruction


def test_rejects_question_properties_and_null_profile_values_that_desktop_rejects() -> None:
    decision = evaluate_personal_assistant_output(
        _fence(
            {
                "type": "task-profile-review",
                "interviewId": "planning-1",
                "revision": 4,
                "task": {"id": "pet-results", "title": "PET results"},
                "progress": {"current": 1, "total": 1},
                "profileFields": [
                    {"id": "pet-next-step", "label": "Next check", "value": None}
                ],
                "question": {
                    "id": "pet-next-step",
                    "profileFieldId": "pet-next-step",
                    "label": "What is the next concrete check?",
                    "text": "What is the next concrete check?",
                    "type": "single-choice",
                    "options": [{"value": "check", "label": "Check results"}],
                },
            }
        ),
        interview=_active_interview(),
    )

    assert decision.accepted is False
    assert decision.reason == "invalid_task_profile_review"


def test_rejects_malformed_hermes_ui_instead_of_exposing_raw_json() -> None:
    decision = evaluate_personal_assistant_output(
        "```hermes-ui\n{not-json}\n```",
        interview=_active_interview(),
    )

    assert decision.accepted is False
    assert decision.reason == "invalid_hermes_ui"


def test_approved_interview_week_plan_still_requires_fresh_calendar() -> None:
    decision = evaluate_personal_assistant_output(
        _fence({"type": "week-planner", "weekStart": "2026-07-20", "days": []}),
        interview=_active_interview(readiness_approved=True),
    )

    assert decision.accepted is False
    assert decision.reason == "calendar_preflight_required"


def test_non_planning_answer_is_not_blocked() -> None:
    decision = evaluate_personal_assistant_output(
        "The clinic opens at 08:00.",
        interview=_active_interview(),
    )

    assert decision.accepted is True


def test_rejects_multi_question_planning_prose_during_active_interview() -> None:
    response = """## תמונת על של שלושת הקורסים

| תוצר מרכזי | אורך | פורמט |
| --- | --- | --- |
| workflow עובד | 4 שיעורים | אונליין |

## מה עדיין צריך לדייק

1. באיזה כלי עובדים בפועל?
2. איזה סוג פרויקטים הכי מתאים לקהל?
3. האם המשתתף מביא פרויקט משלו?
4. האם הקורס צריך להיות סביב מוצר אחד בלבד?
"""

    decision = evaluate_personal_assistant_output(
        response,
        interview=_active_interview(),
        intent_action="general.query",
    )

    assert decision.accepted is False
    assert decision.reason == "unrendered_interview_questions"
    assert "one task-profile-review" in decision.retry_instruction


def test_rejects_static_numbered_plan_during_unfinished_interview() -> None:
    decision = evaluate_personal_assistant_output(
        "## Today's priorities\n1. Call the clinic\n2. Review the PET results\n3. Send documents",
        interview=_active_interview(),
        intent_action="general.query",
    )

    assert decision.accepted is False
    assert decision.reason == "static_plan_during_interview"


def test_timer_start_intent_cannot_complete_with_prose_only() -> None:
    decision = evaluate_personal_assistant_output(
        "Starting the timer now.",
        interview=_active_interview(),
        intent_action="task.timer.start",
    )

    assert decision.accepted is False
    assert decision.reason == "timer_action_not_executed"
    assert "flowstate_start_timer" in decision.retry_instruction


def test_title_based_timer_start_cannot_complete_with_prose_only() -> None:
    decision = evaluate_personal_assistant_output(
        "The start tool is unavailable.",
        interview=_active_interview(),
        intent_action="task.timer.start.lookup",
    )

    assert decision.accepted is False
    assert decision.reason == "timer_action_not_executed"
    assert "search" in decision.retry_instruction.lower()


def test_timer_stop_intent_cannot_complete_with_prose_only() -> None:
    decision = evaluate_personal_assistant_output(
        "הטיימר נעצר.",
        interview=_active_interview(),
        intent_action="task.timer.stop",
    )

    assert decision.accepted is False
    assert decision.reason == "timer_action_not_executed"
    assert "flowstate_stop_timer" in decision.retry_instruction


def test_timer_start_intent_allows_response_after_tool_execution() -> None:
    decision = evaluate_personal_assistant_output(
        "The timer is running.",
        interview=_active_interview(),
        intent_action="task.timer.start",
        timer_action_executed=True,
    )

    assert decision.accepted is True


def test_timer_receipt_rejects_visible_internal_session_identifier() -> None:
    decision = evaluate_personal_assistant_output(
        (
            "הטיימר הופעל עבור להגיע משרות 2+10.\n"
            "מזהה סשן: e5ffe006-379b-4fcc-b6a6-724bce90f148"
        ),
        interview=_active_interview(),
        intent_action="task.timer.start",
        timer_action_executed=True,
    )

    assert decision.accepted is False
    assert decision.reason == "internal_identifier_exposed"
    assert "task name" in decision.retry_instruction


def test_retry_exhaustion_fallback_is_a_matching_interactive_question() -> None:
    fallback = build_safe_interview_fallback(_active_interview())
    decision = evaluate_personal_assistant_output(
        fallback,
        interview=_active_interview(),
        intent_action="workflow.advance",
    )

    assert decision.accepted is True
    assert '"type":"task-profile-review"' in fallback
    assert '"task":{"id":"pet-results"' in fallback


def test_nested_cursor_drives_output_validation_and_safe_fallback() -> None:
    interview = {
        "interviewId": "planning-1",
        "revision": 7,
        "status": "active",
        "cursor": {"taskId": "pet-results", "questionId": "urgency"},
        "tasks": [{"taskId": "pet-results", "title": "Check PET results"}],
    }

    fallback = build_safe_interview_fallback(interview)
    decision = evaluate_personal_assistant_output(
        fallback,
        interview=interview,
        intent_action="workflow.advance",
    )

    assert decision.accepted is True
    assert '"task":{"id":"pet-results"' in fallback
    assert '"id":"urgency","profileFieldId":"urgency"' in fallback
    assert '"title":"Check PET results"' in fallback


def test_safe_fallback_bounds_profile_fields_and_keeps_current_question_reference() -> None:
    profile = {
        key: ["value"] if key in {"dependencies", "risks", "constraints"} else "value"
        for key in (
            "urgency",
            "importance",
            "outcome",
            "dependencies",
            "effort",
            "energy",
            "timing",
            "risks",
            "doneEnough",
            "notes",
            "context",
            "confidence",
            "evidence",
            "constraints",
        )
    }
    interview = {
        "interviewId": "planning-1",
        "revision": 7,
        "status": "active",
        "cursor": {"taskId": "pet-results", "questionId": "urgency"},
        "tasks": [
            {
                "taskId": "pet-results",
                "title": "Check PET results",
                "profile": profile,
            }
        ],
    }

    fallback = build_safe_interview_fallback(interview)
    artifact = json.loads(fallback.removeprefix("```hermes-ui\n").removesuffix("\n```"))
    field_ids = [field["id"] for field in artifact["profileFields"]]

    assert 1 <= len(field_ids) <= 12
    assert len(field_ids) == len(set(field_ids))
    assert artifact["question"]["profileFieldId"] in field_ids
