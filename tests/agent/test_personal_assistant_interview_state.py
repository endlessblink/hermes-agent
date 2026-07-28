import json
from concurrent.futures import ThreadPoolExecutor

import pytest


def _complete_profile():
    return {
        "urgency": "high",
        "importance": "high",
        "outcome": "A concrete outcome",
        "dependencies": [],
        "effort": "15 minutes",
        "energy": "low",
        "timing": "Monday morning",
        "risks": [],
        "doneEnough": "The next action is known",
    }


def test_v1_state_migrates_to_v2_with_no_active_interview(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_text(
        json.dumps({
            "schema_version": 1,
            "version": 7,
            "revision": 7,
            "canonical_session_id": "assistant-home",
            "outcomes": [{"id": "ship", "title": "Ship"}],
        }),
        encoding="utf-8",
    )

    state = store.read()

    assert state["schema_version"] == 2
    assert state["planning_interview"] is None
    assert state["canonical_session_id"] == "assistant-home"
    assert state["outcomes"] == [{"id": "ship", "title": "Ship"}]


def test_future_state_schema_is_rejected_instead_of_downgraded(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_text(
        json.dumps({"schema_version": 99, "version": 1}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="future personal assistant state schema"):
        store.read()


def test_start_interview_is_durable_and_public(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    result = store.patch_planning_interview(
        interview_id="weekly-2026-07-20",
        expected_revision=0,
        request_id="desktop:start:1",
        operations=[
            {
                "op": "start",
                "sourceSnapshot": {
                    "fingerprint": "sources:42",
                    "revisions": {"flowstate": "42", "calendar": "19"},
                },
                "tasks": [
                    {"taskId": "pet-results", "title": "Check PET results"},
                    {"taskId": "job-outreach", "title": "Contact companies"},
                ],
            }
        ],
    )

    interview = result["interview"]
    assert interview["interviewId"] == "weekly-2026-07-20"
    assert interview["interviewRevision"] == 1
    assert interview["status"] == "active"
    assert interview["cursor"] == {"taskId": "pet-results", "questionId": "urgency"}
    assert [task["taskId"] for task in interview["tasks"]] == [
        "pet-results",
        "job-outreach",
    ]
    assert result["duplicate"] is False
    assert PersonalAssistantStateStore(tmp_path).get_planning_interview() == interview
    assert store.public()["planningInterview"] == interview


def test_new_planning_date_atomically_supersedes_and_archives_old_interview(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    for interview_id, planning_date, request_id in (
        ("daily-20", "2026-07-20", "start-20"),
        ("daily-21", "2026-07-21", "start-21"),
    ):
        store.patch_planning_interview(
            interview_id=interview_id,
            expected_revision=0,
            request_id=request_id,
            operations=[{
                "op": "start",
                "planningDate": planning_date,
                "sourceSnapshot": {"fingerprint": planning_date},
                "tasks": [{"taskId": f"task-{planning_date}", "title": "Task"}],
            }],
        )

    state = store.read()
    assert state["planning_interview"]["interviewId"] == "daily-21"
    assert state["planning_interview_archive"][-1]["interviewId"] == "daily-20"
    assert state["planning_interview_archive"][-1]["status"] == "superseded"


def test_interview_request_replay_is_idempotent_and_digest_bound(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    start = {
        "op": "start",
        "sourceSnapshot": {"fingerprint": "sources:1"},
        "tasks": [{"taskId": "pet-results", "title": "Check PET results"}],
    }
    first = store.patch_planning_interview(
        interview_id="weekly",
        expected_revision=0,
        request_id="request-1",
        operations=[start],
    )
    replay = store.patch_planning_interview(
        interview_id="weekly",
        expected_revision=0,
        request_id="request-1",
        operations=[start],
    )

    assert replay["duplicate"] is True
    assert replay["interview"] == first["interview"]
    assert replay["stateVersion"] == first["stateVersion"]

    with pytest.raises(ValueError, match="different payload"):
        store.patch_planning_interview(
            interview_id="weekly",
            expected_revision=0,
            request_id="request-1",
            operations=[{**start, "tasks": [{"taskId": "other", "title": "Other"}]}],
        )


def test_interview_cas_is_independent_and_conflicts_return_latest(tmp_path):
    from agent.personal_assistant_state import (
        InterviewRevisionConflict,
        PersonalAssistantStateStore,
    )

    store = PersonalAssistantStateStore(tmp_path)
    started = store.patch_planning_interview(
        interview_id="weekly",
        expected_revision=0,
        request_id="start",
        operations=[
            {
                "op": "start",
                "sourceSnapshot": {},
                "tasks": [
                    {
                        "taskId": "pet",
                        "title": "Check PET results",
                        "profile": _complete_profile(),
                    }
                ],
            }
        ],
    )
    store.patch("edit", {"focus": "unrelated change"})

    paused = store.patch_planning_interview(
        interview_id="weekly",
        expected_revision=started["interview"]["interviewRevision"],
        request_id="pause",
        operations=[{"op": "pause"}],
    )

    assert paused["interview"]["status"] == "paused"
    assert paused["interview"]["interviewRevision"] == 2
    with pytest.raises(InterviewRevisionConflict) as raised:
        store.patch_planning_interview(
            interview_id="weekly",
            expected_revision=1,
            request_id="stale-resume",
            operations=[{"op": "resume"}],
        )
    assert raised.value.current_revision == 2
    assert raised.value.latest == paused["interview"]

    resumed = store.patch_planning_interview(
        interview_id="weekly",
        expected_revision=2,
        request_id="resume",
        operations=[{"op": "resume"}],
    )
    assert resumed["interview"]["status"] == "active"
    assert resumed["interview"]["interviewRevision"] == 3


def test_simultaneous_client_edits_allow_exactly_one_revision_winner(tmp_path):
    from agent.personal_assistant_state import (
        InterviewRevisionConflict,
        PersonalAssistantStateStore,
    )

    store = PersonalAssistantStateStore(tmp_path)
    store.patch_planning_interview(
        interview_id="weekly",
        expected_revision=0,
        request_id="start",
        operations=[
            {
                "op": "start",
                "sourceSnapshot": {},
                "tasks": [{"taskId": "pet", "title": "Check PET results"}],
            }
        ],
    )

    def pause(request_id):
        try:
            return store.patch_planning_interview(
                interview_id="weekly",
                expected_revision=1,
                request_id=request_id,
                operations=[{"op": "pause"}],
            )
        except InterviewRevisionConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(pause, ["desktop:pause", "telegram:pause"]))

    assert sum(isinstance(result, InterviewRevisionConflict) for result in results) == 1
    assert sum(isinstance(result, dict) for result in results) == 1
    assert store.get_planning_interview()["interviewRevision"] == 2


def test_task_profile_edit_confirmation_and_advance_are_atomic(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    started = store.patch_planning_interview(
        interview_id="weekly",
        expected_revision=0,
        request_id="start",
        operations=[
            {
                "op": "start",
                "sourceSnapshot": {},
                "tasks": [
                    {"taskId": "pet", "title": "Check PET results"},
                    {"taskId": "outreach", "title": "Contact companies"},
                ],
            }
        ],
    )

    changed = store.patch_planning_interview(
        interview_id="weekly",
        expected_revision=started["interview"]["interviewRevision"],
        request_id="answer-pet",
        operations=[
            {
                "op": "patch-task",
                "taskId": "pet",
                "fieldEdits": {
                    "urgency": "high",
                    "importance": "high",
                    "outcome": "Know whether results arrived and the next action",
                    "dependencies": [],
                    "effort": "15 minutes",
                    "energy": "low",
                    "timing": "Monday morning",
                    "risks": ["A medical follow-up could be delayed"],
                    "doneEnough": "The result status and next action are known",
                },
                "breakdown": ["Open the results portal", "Identify the next action"],
            },
            {"op": "confirm-task", "taskId": "pet"},
        ],
    )

    pet = changed["interview"]["tasks"][0]
    assert pet["profile"]["urgency"] == "high"
    assert pet["breakdown"] == ["Open the results portal", "Identify the next action"]
    assert pet["confirmed"] is True
    assert changed["interview"]["cursor"] == {
        "taskId": "outreach",
        "questionId": "urgency",
    }
    assert changed["interview"]["interviewRevision"] == 2


def test_incomplete_task_profile_cannot_be_confirmed(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    store.patch_planning_interview(
        interview_id="weekly",
        expected_revision=0,
        request_id="start",
        operations=[
            {
                "op": "start",
                "sourceSnapshot": {},
                "tasks": [{"taskId": "pet", "title": "Check PET results"}],
            }
        ],
    )

    with pytest.raises(ValueError, match="profile is incomplete"):
        store.patch_planning_interview(
            interview_id="weekly",
            expected_revision=1,
            request_id="confirm-too-soon",
            operations=[{"op": "confirm-task", "taskId": "pet"}],
        )


def test_readiness_and_record_plan_are_gated_by_confirmed_profiles(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    started = store.patch_planning_interview(
        interview_id="weekly",
        expected_revision=0,
        request_id="start",
        operations=[
            {
                "op": "start",
                "sourceSnapshot": {},
                "tasks": [
                    {
                        "taskId": "pet",
                        "title": "Check PET results",
                        "profile": _complete_profile(),
                    }
                ],
            }
        ],
    )

    with pytest.raises(ValueError, match="confirmed or deferred"):
        store.patch_planning_interview(
            interview_id="weekly",
            expected_revision=1,
            request_id="early-ready",
            operations=[{"op": "approve-readiness"}],
        )
    with pytest.raises(ValueError, match="readiness approval"):
        store.patch_planning_interview(
            interview_id="weekly",
            expected_revision=1,
            request_id="early-plan",
            operations=[{"op": "record-plan", "plan": {"artifactId": "week-1"}}],
        )

    ready = store.patch_planning_interview(
        interview_id="weekly",
        expected_revision=started["interview"]["interviewRevision"],
        request_id="confirm-and-ready",
        operations=[
            {"op": "confirm-task", "taskId": "pet"},
            {"op": "approve-readiness"},
        ],
    )
    completed = store.patch_planning_interview(
        interview_id="weekly",
        expected_revision=ready["interview"]["interviewRevision"],
        request_id="record-plan",
        operations=[
            {
                "op": "record-plan",
                "plan": {"artifactId": "week-1", "status": "drafted"},
            }
        ],
    )

    assert ready["interview"]["readinessApproved"] is True
    assert ready["interview"]["readinessApprovedAt"]
    assert completed["interview"]["plan"]["artifactId"] == "week-1"
    assert completed["interview"]["status"] == "completed"
    assert completed["interview"]["completedAt"]


def test_cancel_and_forget_end_or_clear_the_interview(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    store.patch_planning_interview(
        interview_id="weekly",
        expected_revision=0,
        request_id="start",
        operations=[
            {
                "op": "start",
                "sourceSnapshot": {},
                "tasks": [{"taskId": "pet", "title": "Check PET results"}],
            }
        ],
    )
    canceled = store.patch_planning_interview(
        interview_id="weekly",
        expected_revision=1,
        request_id="cancel",
        operations=[{"op": "cancel"}],
    )

    assert canceled["interview"]["status"] == "canceled"
    assert canceled["interview"]["completedAt"]

    forgotten = store.patch("forget", {})
    assert forgotten["planning_interview"] is None
    assert store.public()["planningInterview"] is None


def test_add_defer_and_revisit_task_controls_preserve_review_order(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    store.patch_planning_interview(
        interview_id="weekly",
        expected_revision=0,
        request_id="start",
        operations=[
            {
                "op": "start",
                "sourceSnapshot": {},
                "tasks": [{"taskId": "pet", "title": "Check PET results"}],
            }
        ],
    )
    changed = store.patch_planning_interview(
        interview_id="weekly",
        expected_revision=1,
        request_id="manage-tasks",
        operations=[
            {
                "op": "add-task",
                "task": {"taskId": "outreach", "title": "Contact companies"},
            },
            {"op": "defer-task", "taskId": "pet", "reason": "Waiting for the portal"},
            {"op": "set-cursor", "taskId": "outreach", "questionId": "urgency"},
        ],
    )

    assert changed["interview"]["tasks"][0]["deferred"] is True
    assert (
        changed["interview"]["tasks"][0]["profile"]["deferralReason"]
        == "Waiting for the portal"
    )
    assert changed["interview"]["cursor"] == {
        "taskId": "outreach",
        "questionId": "urgency",
    }


def test_ui_response_controller_persists_answer_and_advances_question(tmp_path):
    from agent.personal_assistant_interview import PlanningInterviewController
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    store.patch_planning_interview(
        interview_id="weekly",
        expected_revision=0,
        request_id="start",
        operations=[
            {
                "op": "start",
                "sourceSnapshot": {},
                "tasks": [{"taskId": "pet", "title": "Check PET results"}],
            }
        ],
    )
    controller = PlanningInterviewController(store)

    result = controller.respond({
        "interviewId": "weekly",
        "expectedRevision": 1,
        "taskId": "pet",
        "questionId": "urgency",
        "requestId": "telegram:answer:1",
        "response": {
            "selectedValues": ["high"],
            "customAnswer": None,
            "fieldEdits": {"notes": "Results affect the next medical step"},
            "action": "answer",
        },
    })

    task = result["interview"]["tasks"][0]
    assert task["profile"]["urgency"] == "high"
    assert task["profile"]["notes"] == "Results affect the next medical step"
    assert result["interview"]["cursor"] == {
        "taskId": "pet",
        "questionId": "importance",
    }
    assert result["receipt"]["requestId"] == "telegram:answer:1"


def test_daily_grounding_interview_asks_about_the_day_and_becomes_ready(tmp_path):
    from agent.personal_assistant_interview import PlanningInterviewController
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    started = store.patch_planning_interview(
        interview_id="today",
        expected_revision=0,
        request_id="start-today",
        operations=[
            {
                "op": "start",
                "mode": "daily-grounding",
                "planningDate": "2026-07-22",
                "sourceSnapshot": {},
                "tasks": [{"taskId": "day-context", "title": "תכנון שאר היום"}],
            }
        ],
    )

    assert started["interview"]["mode"] == "daily-grounding"
    assert started["interview"]["cursor"] == {
        "taskId": "day-context",
        "questionId": "energy",
    }

    revision = 1
    answers = {
        "energy": "medium",
        "workBoundary": "21:30",
        "hardCommitments": "מפגש עם תמר",
        "location": "בבית",
    }
    result = started
    for question_id, answer in answers.items():
        result = PlanningInterviewController(store).respond(
            {
                "interviewId": "today",
                "expectedRevision": revision,
                "taskId": "day-context",
                "questionId": question_id,
                "requestId": f"answer-{question_id}",
                "response": {"customAnswer": answer, "action": "answer"},
            }
        )
        revision += 1

    interview = result["interview"]
    assert interview["tasks"][0]["profile"] == answers
    assert interview["tasks"][0]["confirmed"] is True
    assert interview["readinessApproved"] is True


def test_future_day_grounding_asks_one_availability_question_and_becomes_ready(tmp_path):
    from agent.personal_assistant_interview import PlanningInterviewController
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    started = store.patch_planning_interview(
        interview_id="tomorrow",
        expected_revision=0,
        request_id="start-tomorrow",
        operations=[
            {
                "op": "start",
                "mode": "daily-grounding",
                "planningDate": "2099-01-02",
                "questionOrder": ["availability"],
                "sourceSnapshot": {},
                "tasks": [{"taskId": "day-context", "title": "תכנון מחר"}],
            }
        ],
    )

    interview = started["interview"]
    assert interview["cursor"] == {"taskId": "day-context", "questionId": "availability"}

    result = PlanningInterviewController(store).respond(
        {
            "interviewId": "tomorrow",
            "expectedRevision": 1,
            "taskId": "day-context",
            "questionId": "availability",
            "requestId": "answer-availability",
            "response": {"selectedValues": ["09:00-21:00-buffered"], "action": "answer"},
        }
    )
    completed = result["interview"]
    assert completed["tasks"][0]["profile"] == {"availability": "09:00-21:00-buffered"}
    assert completed["tasks"][0]["confirmed"] is True
    assert completed["readinessApproved"] is True


def test_daily_grounding_answers_survive_a_new_store_after_every_response(tmp_path):
    from agent.personal_assistant_interview import PlanningInterviewController
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    store.patch_planning_interview(
        interview_id="restartable-today",
        expected_revision=0,
        request_id="start-restartable-today",
        operations=[
            {
                "op": "start",
                "mode": "daily-grounding",
                "planningDate": "2026-07-22",
                "sourceSnapshot": {},
                "tasks": [{"taskId": "day-context", "title": "תכנון שאר היום"}],
            }
        ],
    )
    answers = [
        ("energy", "medium"),
        ("workBoundary", "21:30"),
        ("hardCommitments", "מפגש עם תמר"),
        ("location", "בבית"),
    ]

    for revision, (question_id, answer) in enumerate(answers, start=1):
        restarted_store = PersonalAssistantStateStore(tmp_path)
        current = restarted_store.get_planning_interview()
        assert current is not None
        assert current["cursor"]["questionId"] == question_id
        PlanningInterviewController(restarted_store).respond(
            {
                "interviewId": "restartable-today",
                "expectedRevision": revision,
                "taskId": "day-context",
                "questionId": question_id,
                "requestId": f"restart-answer-{question_id}",
                "response": {"customAnswer": answer, "action": "answer"},
            }
        )

    final = PersonalAssistantStateStore(tmp_path).get_planning_interview()
    assert final is not None
    assert final["tasks"][0]["profile"] == dict(answers)
    assert final["readinessApproved"] is True


def test_ui_response_controller_confirms_and_advances_after_final_answer(tmp_path):
    from agent.personal_assistant_interview import PlanningInterviewController
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    profile = _complete_profile()
    profile.pop("doneEnough")
    store.patch_planning_interview(
        interview_id="weekly",
        expected_revision=0,
        request_id="start",
        operations=[
            {
                "op": "start",
                "sourceSnapshot": {},
                "tasks": [
                    {"taskId": "pet", "title": "Check PET results", "profile": profile},
                    {"taskId": "outreach", "title": "Contact companies"},
                ],
            },
        ],
    )
    store.patch_planning_interview(
        interview_id="weekly",
        expected_revision=1,
        request_id="go-to-final-question",
        operations=[
            {"op": "set-cursor", "taskId": "pet", "questionId": "doneEnough"},
        ],
    )

    result = PlanningInterviewController(store).respond({
        "interviewId": "weekly",
        "expectedRevision": 2,
        "taskId": "pet",
        "questionId": "doneEnough",
        "requestId": "desktop:answer-final",
        "response": {
            "customAnswer": "The result status and next action are known",
            "action": "answer",
        },
    })

    assert result["interview"]["tasks"][0]["confirmed"] is True
    assert result["interview"]["cursor"] == {
        "taskId": "outreach",
        "questionId": "urgency",
    }


def test_ui_response_rejects_a_task_or_question_outside_current_cursor(tmp_path):
    from agent.personal_assistant_interview import PlanningInterviewController
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    store.patch_planning_interview(
        interview_id="weekly",
        expected_revision=0,
        request_id="start",
        operations=[
            {
                "op": "start",
                "sourceSnapshot": {},
                "tasks": [
                    {"taskId": "pet", "title": "Check PET results"},
                    {"taskId": "outreach", "title": "Contact companies"},
                ],
            }
        ],
    )
    controller = PlanningInterviewController(store)

    with pytest.raises(ValueError, match="current question"):
        controller.respond({
            "interviewId": "weekly",
            "expectedRevision": 1,
            "taskId": "pet",
            "questionId": "importance",
            "requestId": "stale-question",
            "response": {"selectedValues": ["high"], "action": "answer"},
        })
    with pytest.raises(ValueError, match="current task"):
        controller.respond({
            "interviewId": "weekly",
            "expectedRevision": 1,
            "taskId": "outreach",
            "questionId": "urgency",
            "requestId": "stale-task",
            "response": {"selectedValues": ["high"], "action": "answer"},
        })


def test_ui_response_controller_translates_confirm_and_pause_actions(tmp_path):
    from agent.personal_assistant_interview import PlanningInterviewController
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    store.patch_planning_interview(
        interview_id="weekly",
        expected_revision=0,
        request_id="start",
        operations=[
            {
                "op": "start",
                "sourceSnapshot": {},
                "tasks": [
                    {
                        "taskId": "pet",
                        "title": "Check PET results",
                        "profile": _complete_profile(),
                    },
                    {"taskId": "outreach", "title": "Contact companies"},
                ],
            }
        ],
    )
    controller = PlanningInterviewController(store)
    confirmed = controller.respond({
        "interviewId": "weekly",
        "expectedRevision": 1,
        "taskId": "pet",
        "questionId": "doneEnough",
        "requestId": "desktop:confirm:1",
        "response": {"action": "confirm"},
    })
    paused = controller.respond({
        "interviewId": "weekly",
        "expectedRevision": 2,
        "taskId": "outreach",
        "questionId": "urgency",
        "requestId": "telegram:pause:1",
        "response": {"action": "pause"},
    })

    assert confirmed["interview"]["tasks"][0]["confirmed"] is True
    assert confirmed["interview"]["cursor"]["taskId"] == "outreach"
    assert paused["interview"]["status"] == "paused"
