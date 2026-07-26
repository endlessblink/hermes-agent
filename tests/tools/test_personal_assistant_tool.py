import json


def test_calendar_preflight_persists_receipt_in_public_state(monkeypatch, tmp_path):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    monkeypatch.setattr(
        pat,
        "build_calendar_preflight_receipt",
        lambda **_kwargs: {
            "status": "complete",
            "complete": True,
            "capturedAt": "2026-07-21T06:00:00+00:00",
            "expiresAt": "2026-07-21T06:15:00+00:00",
            "range": {"startDate": "2026-07-21", "endDate": "2026-07-22"},
        },
    )

    result = json.loads(
        pat._handle_calendar_preflight(
            {
                "startDate": "2026-07-21",
                "endDate": "2026-07-22",
                "timezone": "Asia/Jerusalem",
            }
        )
    )
    state = json.loads(pat._handle_get_state({"mode": "full"}))["result"]["state"]

    assert result["result"]["receipt"]["status"] == "complete"
    assert state["calendarPreflightReceipt"]["complete"] is True


def test_propose_capture_is_deduplicated_and_visible_in_assistant_state(monkeypatch, tmp_path):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    args = {
        "section": "commitments",
        "title": "Send the proposal",
        "evidence": "I promised to send it tomorrow.",
        "sourceSessionId": "chat-1",
    }

    first = json.loads(pat._handle_propose_capture(args))
    second = json.loads(pat._handle_propose_capture(args))
    state = json.loads(pat._handle_get_state({"mode": "full"}))

    assert first["result"]["proposal"]["id"] == second["result"]["proposal"]["id"]
    assert first["result"]["stateVersion"] == second["result"]["stateVersion"]
    assert len(state["result"]["state"]["captureProposals"]) == 1
    assert state["result"]["state"]["captureProposals"][0]["status"] == "pending"


def test_propose_capture_deduplicates_equivalent_wording_but_keeps_changed_values(monkeypatch, tmp_path):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    first = json.loads(
        pat._handle_propose_capture(
            {
                "section": "preferences",
                "title": "המשימה „להגיש משרות” מתוכננת כבלוק של 40 דקות ובעדיפות גבוהה.",
                "evidence": "המשתמש אמר שהמשימה אורכת 40 דקות ובעדיפות גבוהה.",
            }
        )
    )["result"]["proposal"]
    equivalent = json.loads(
        pat._handle_propose_capture(
            {
                "section": "preferences",
                "title": "„להגיש משרות”: 40 דקות ובעדיפות גבוהה",
                "evidence": "המשתמש קבע במפורש 40 דקות ועדיפות גבוהה.",
            }
        )
    )["result"]["proposal"]
    changed = json.loads(
        pat._handle_propose_capture(
            {
                "section": "preferences",
                "title": "„להגיש משרות”: 50 דקות ובעדיפות גבוהה",
                "evidence": "המשתמש שינה ל־50 דקות ועדיפות גבוהה.",
            }
        )
    )["result"]["proposal"]
    state = json.loads(pat._handle_get_state({"mode": "full"}))["result"]["state"]

    assert equivalent["id"] == first["id"]
    assert changed["id"] != first["id"]
    assert len(state["captureProposals"]) == 2


def test_repeated_capture_does_not_reset_a_reviewed_proposal(monkeypatch, tmp_path):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    args = {
        "section": "commitments",
        "title": "Send the proposal",
        "evidence": "I promised to send it tomorrow.",
        "sourceSessionId": "chat-1",
    }
    proposal = json.loads(pat._handle_propose_capture(args))["result"]["proposal"]
    pat._handle_state_change(
        {
            "operations": [
                {
                    "op": "upsert",
                    "section": "captureProposals",
                    "id": proposal["id"],
                    "value": {"status": "accepted"},
                }
            ],
            "preview": False,
            "requestId": "accept-capture",
        }
    )

    repeated = json.loads(pat._handle_propose_capture(args))

    assert repeated["result"]["proposal"]["status"] == "accepted"


def test_idempotency_key_is_bound_to_the_approved_operations(monkeypatch, tmp_path):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    base = {
        "operations": [
            {
                "op": "upsert",
                "section": "outcomes",
                "id": "outcome-1",
                "value": {"title": "First"},
            }
        ],
        "preview": False,
        "requestId": "approved-change",
    }
    assert "result" in json.loads(pat._handle_state_change(base))

    conflicting = json.loads(
        pat._handle_state_change(
            {
                **base,
                "operations": [
                    {
                        "op": "upsert",
                        "section": "outcomes",
                        "id": "outcome-1",
                        "value": {"title": "Different"},
                    }
                ],
            }
        )
    )

    assert "already used for different operations" in conflicting["error"]


def test_state_change_previews_by_default_and_apply_requires_request_id(monkeypatch, tmp_path):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    operation = {
        "op": "upsert",
        "section": "outcomes",
        "id": "outcome-1",
        "value": {"title": "Ship the proposal", "status": "active"},
    }

    preview = json.loads(pat._handle_state_change({"operations": [operation]}))
    rejected = json.loads(
        pat._handle_state_change({"operations": [operation], "preview": False})
    )
    state = json.loads(pat._handle_get_state({}))

    assert preview["result"]["preview"] is True
    assert "requestId is required" in rejected["error"]
    assert state["result"]["state"]["outcomes"] == []


def test_state_change_applies_once_with_optimistic_version(monkeypatch, tmp_path):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    operation = {
        "op": "upsert",
        "section": "preferences",
        "id": "preference-1",
        "value": {"title": "Keep plans compact"},
    }

    applied = json.loads(
        pat._handle_state_change(
            {
                "expectedVersion": 0,
                "operations": [operation],
                "preview": False,
                "requestId": "approved-change-1",
            }
        )
    )
    replay = json.loads(
        pat._handle_state_change(
            {
                "expectedVersion": 0,
                "operations": [operation],
                "preview": False,
                "requestId": "approved-change-1",
            }
        )
    )

    assert applied["result"]["preview"] is False
    assert applied["result"]["state"]["preferences"][0]["id"] == "preference-1"
    assert replay["result"]["replayed"] is True


def test_tools_fail_closed_outside_office_work(monkeypatch, tmp_path):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("default", tmp_path))

    result = json.loads(pat._handle_get_state({}))

    assert "office-work" in result["error"]


def test_personal_assistant_toolset_exposes_state_parity_tools():
    from toolsets import get_toolset

    assert set(get_toolset("personal_assistant")["tools"]) == {
        "personal_assistant_get_state",
        "personal_assistant_calendar_preflight",
        "personal_assistant_interview_start",
        "personal_assistant_reconcile_inventory",
        "personal_assistant_propose_capture",
        "personal_assistant_state_change",
        "personal_assistant_safety_review",
        "suggestion_rule_save",
    }


def test_safety_review_schema_forbids_naive_datetimes():
    from tools.personal_assistant_tool import SAFETY_REVIEW_SCHEMA

    item_properties = SAFETY_REVIEW_SCHEMA["parameters"]["properties"]["protectedItems"]["items"]["properties"]
    for field in ("deadline", "nextReviewAt"):
        description = item_properties[field]["description"]
        assert "Never send a datetime without Z or a numeric UTC offset" in description
        assert "Prefer YYYY-MM-DD when no time is needed" in description


def test_interview_start_persists_flowstate_task_snapshot(monkeypatch, tmp_path):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    payload = {
        "interviewId": "weekly-2026-07-20",
        "requestId": "start-weekly-2026-07-20",
        "mode": "task-review",
        "sourceSnapshot": {
            "source": "FlowState",
            "fresh": True,
            "revision": "inventory-17",
        },
        "tasks": [
            {"taskId": "pet-results", "title": "Check PET results"},
            {"taskId": "job-search", "title": "Direct company outreach"},
        ],
    }

    first = json.loads(pat._handle_interview_start(payload))
    replay = json.loads(pat._handle_interview_start(payload))

    assert first["result"]["interview"]["cursor"] == {
        "taskId": "pet-results",
        "questionId": "urgency",
    }
    assert first["result"]["interview"]["sourceSnapshot"]["fresh"] is True
    assert replay["result"]["duplicate"] is True


def test_interview_start_resumes_the_active_interview_instead_of_erroring(
    monkeypatch, tmp_path
):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    first = {
        "interviewId": "weekly-2026-07-20",
        "requestId": "start-weekly-2026-07-20",
        "sourceSnapshot": {"source": "FlowState", "fresh": True},
        "tasks": [{"taskId": "pet-results", "title": "Check PET results"}],
    }
    json.loads(pat._handle_interview_start(first))

    resumed = json.loads(
        pat._handle_interview_start(
            {
                "interviewId": "today-2026-07-21",
                "requestId": "start-today-2026-07-21",
                "sourceSnapshot": {"source": "FlowState", "fresh": True},
                "tasks": [{"taskId": "job-search", "title": "Company outreach"}],
            }
        )
    )

    assert "error" not in resumed
    assert resumed["result"]["resumed"] is True
    assert resumed["result"]["interview"]["interviewId"] == "weekly-2026-07-20"
    assert resumed["result"]["requestedInterviewId"] == "today-2026-07-21"


def test_interview_resume_refreshes_source_snapshot_without_losing_answers(
    monkeypatch, tmp_path
):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    first = json.loads(
        pat._handle_interview_start(
            {
                "interviewId": "planning-2026-07-24",
                "requestId": "start-planning",
                "planningDate": "2026-07-24",
                "mode": "daily-grounding",
                "sourceSnapshot": {
                    "calendarReceipt": {"capturedAt": "2026-07-24T08:00:00Z"}
                },
                "tasks": [{"taskId": "day-context", "title": "תכנון שאר היום"}],
            }
        )
    )["result"]["interview"]
    store = pat._store()
    answered = store.patch_planning_interview(
        interview_id=first["interviewId"],
        expected_revision=first["interviewRevision"],
        request_id="answer-energy",
        operations=[
            {
                "op": "patch-task",
                "taskId": "day-context",
                "fieldEdits": {"energy": "medium"},
            }
        ],
    )["interview"]

    resumed = json.loads(
        pat._handle_interview_start(
            {
                "interviewId": "planning-2026-07-24",
                "requestId": "refresh-calendar",
                "planningDate": "2026-07-24",
                "mode": "daily-grounding",
                "sourceSnapshot": {
                    "calendarReceipt": {"capturedAt": "2026-07-24T09:00:00Z"}
                },
                "tasks": [{"taskId": "day-context", "title": "תכנון שאר היום"}],
            }
        )
    )["result"]

    assert resumed["resumed"] is True
    assert resumed["sourceSnapshotRefreshed"] is True
    assert resumed["interview"]["sourceSnapshot"]["calendarReceipt"]["capturedAt"] == (
        "2026-07-24T09:00:00Z"
    )
    assert resumed["interview"]["tasks"][0]["profile"]["energy"] == "medium"
    assert resumed["interview"]["interviewRevision"] == answered["interviewRevision"] + 1


def test_interview_start_resumes_legacy_interview_for_its_effective_date(
    monkeypatch, tmp_path
):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    json.loads(
        pat._handle_interview_start(
            {
                "interviewId": "legacy-evening-plan",
                "requestId": "start-legacy-evening-plan",
                "sourceSnapshot": {"localDate": "2026-07-20"},
                "tasks": [{"taskId": "pet-results", "title": "Check PET results"}],
            }
        )
    )

    resumed = json.loads(
        pat._handle_interview_start(
            {
                "interviewId": "retry-evening-plan",
                "requestId": "retry-legacy-evening-plan",
                "planningDate": "2026-07-20",
                "sourceSnapshot": {"localDate": "2026-07-20"},
                "tasks": [{"taskId": "job-search", "title": "Company outreach"}],
            }
        )
    )

    assert "error" not in resumed
    assert resumed["result"]["resumed"] is True
    assert resumed["result"]["interview"]["interviewId"] == "legacy-evening-plan"


def test_interview_start_supersedes_legacy_interview_for_a_new_date(
    monkeypatch, tmp_path
):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    json.loads(
        pat._handle_interview_start(
            {
                "interviewId": "legacy-evening-plan",
                "requestId": "start-legacy-evening-plan",
                "sourceSnapshot": {"localDate": "2026-07-20"},
                "tasks": [{"taskId": "pet-results", "title": "Check PET results"}],
            }
        )
    )

    started = json.loads(
        pat._handle_interview_start(
            {
                "interviewId": "today-plan",
                "requestId": "start-today-plan",
                "planningDate": "2026-07-21",
                "sourceSnapshot": {"localDate": "2026-07-21"},
                "tasks": [{"taskId": "job-search", "title": "Company outreach"}],
            }
        )
    )

    state = pat._store().read()
    assert "error" not in started
    assert started["result"]["interview"]["interviewId"] == "today-plan"
    assert state["planning_interview_archive"][-1]["interviewId"] == "legacy-evening-plan"
    assert state["planning_interview_archive"][-1]["status"] == "superseded"


def test_interview_start_limits_a_future_day_to_one_availability_question(monkeypatch, tmp_path):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    started = json.loads(
        pat._handle_interview_start(
            {
                "interviewId": "future-day",
                "requestId": "start-future-day",
                "mode": "daily-grounding",
                "planningDate": "2099-01-02",
                "sourceSnapshot": {},
            }
        )
    )["result"]["interview"]

    assert started["questionOrder"] == ["availability"]
    assert started["cursor"] == {"taskId": "day-context", "questionId": "availability"}
    assert started["tasks"][0]["title"] == "תכנון מחר"


def test_safety_review_atomically_registers_items_and_records_coverage(monkeypatch, tmp_path):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    payload = {
        "cadence": "daily",
        "scopeFingerprint": "flowstate:sequence-42",
        "sources": [{"id": "flowstate", "status": "fresh", "revision": "42"}],
        "protectedItems": [
            {
                "id": "flowstate:health-blood-test",
                "source": "flowstate",
                "sourceId": "health-blood-test",
                "kind": "commitment",
                "title": "Arrange the required blood test",
                "consequence": "Surgery preparation can be delayed",
                "disposition": "actionable",
                "nextAction": "Call the clinic",
            }
        ],
        "reviewedItemIds": ["flowstate:health-blood-test"],
        "riskItemIds": ["flowstate:health-blood-test"],
        "unresolvedItemIds": [],
    }

    result = json.loads(pat._handle_safety_review(payload))["result"]
    state = json.loads(pat._handle_get_state({"mode": "full"}))["result"]["state"]

    assert result["receipt"]["complete"] is True
    assert result["receipt"]["allClear"] is False
    assert state["protectedItems"][0]["id"] == "flowstate:health-blood-test"
    assert state["latestCoverageReceipt"]["scopeFingerprint"] == "flowstate:sequence-42"


def test_safety_review_safely_schedules_missing_context_for_follow_up(monkeypatch, tmp_path):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    payload = {
        "cadence": "daily",
        "scopeFingerprint": "notion:sequence-42",
        "sources": [{"id": "notion", "status": "fresh", "revision": "42"}],
        "protectedItems": [
            {
                "id": "notion:unclear-project",
                "source": "notion",
                "sourceId": "unclear-project",
                "kind": "commitment",
                "title": "Clarify the project",
                "consequence": "The commitment could be missed",
                "disposition": "needs_context",
                "missingFields": ["next action"],
            }
        ],
        "reviewedItemIds": ["notion:unclear-project"],
        "riskItemIds": [],
        "unresolvedItemIds": [],
    }

    result = json.loads(pat._handle_safety_review(payload))["result"]
    state = json.loads(pat._handle_get_state({"mode": "full"}))["result"]["state"]
    protected_item = state["protectedItems"][0]

    assert result["receipt"]["complete"] is True
    assert result["receipt"]["allClear"] is True
    assert result["receipt"]["unresolvedItemIds"] == []
    assert protected_item["disposition"] == "needs_context"
    assert protected_item["nextReviewAt"].startswith(
        (pat.datetime.now(pat.ZoneInfo("Asia/Jerusalem")).date() + pat.timedelta(days=1)).isoformat()
    )


def test_safety_review_reuses_prior_exact_review_only_when_source_revisions_match(
    monkeypatch, tmp_path
):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    base = {
        "cadence": "daily",
        "scopeFingerprint": "flowstate:sequence-42",
        "sources": [{"id": "flowstate", "status": "fresh", "revision": "42"}],
        "protectedItems": [
            {
                "id": "flowstate:health-blood-test",
                "source": "flowstate",
                "sourceId": "health-blood-test",
                "kind": "commitment",
                "title": "Arrange the required blood test",
                "consequence": "Surgery preparation can be delayed",
                "disposition": "actionable",
                "nextAction": "Call the clinic",
            }
        ],
        "reviewedItemIds": ["flowstate:health-blood-test"],
        "riskItemIds": [],
        "unresolvedItemIds": [],
    }
    first = json.loads(pat._handle_safety_review(base))["result"]
    assert first["receipt"]["complete"] is True

    reused = json.loads(
        pat._handle_safety_review(
            {
                **base,
                "protectedItems": [],
                "reviewedItemIds": [],
                "reusePriorReview": True,
            }
        )
    )["result"]

    assert reused["receipt"]["complete"] is True
    assert reused["reusedPriorReview"] is True

    changed = json.loads(
        pat._handle_safety_review(
            {
                **base,
                "sources": [{"id": "flowstate", "status": "fresh", "revision": "43"}],
                "protectedItems": [],
                "reviewedItemIds": [],
                "reusePriorReview": True,
            }
        )
    )["result"]

    assert changed["receipt"]["complete"] is False
    assert changed["reusedPriorReview"] is False


def test_safety_review_reuses_same_day_review_when_only_calendar_capture_time_changes(
    monkeypatch, tmp_path
):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    base = {
        "cadence": "daily",
        "scopeFingerprint": "2026-07-23|flowstate:644|calendar:2026-07-23T13:17:12Z",
        "sources": [{"id": "flowstate", "status": "fresh", "revision": "644"}],
        "protectedItems": [
            {
                "id": "flowstate:medication",
                "source": "flowstate",
                "sourceId": "medication",
                "kind": "commitment",
                "title": "Order medication",
                "consequence": "Medication may run out",
                "disposition": "actionable",
                "nextAction": "Order it",
            }
        ],
        "reviewedItemIds": ["flowstate:medication"],
        "riskItemIds": [],
        "unresolvedItemIds": [],
    }
    first = json.loads(pat._handle_safety_review(base))["result"]
    assert first["receipt"]["complete"] is True

    reused = json.loads(
        pat._handle_safety_review(
            {
                **base,
                "scopeFingerprint": "2026-07-23|flowstate:644|calendar:2026-07-23T13:32:37Z",
                "protectedItems": [],
                "reviewedItemIds": [],
                "reusePriorReview": True,
            }
        )
    )["result"]

    assert reused["reusedPriorReview"] is True
    assert reused["receipt"]["complete"] is True


def test_safety_review_requires_exact_configured_source_names(monkeypatch, tmp_path):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    pat._store().set_task_source_manifest(
        [{"id": "flowstate", "inventoryTool": "flowstate_list_tasks", "available": True}]
    )
    state = json.loads(pat._handle_get_state({"mode": "full"}))["result"]["state"]
    assert state["taskSourceManifest"] == [
        {"id": "flowstate", "inventoryTool": "flowstate_list_tasks", "available": True}
    ]

    result = json.loads(
        pat._handle_safety_review(
            {
                "cadence": "daily",
                "scopeFingerprint": "flowstate:sequence-42",
                "sources": [
                    {"id": "flowstate-open-personal", "status": "fresh", "revision": "42"}
                ],
                "protectedItems": [],
                "reviewedItemIds": [],
                "riskItemIds": [],
                "unresolvedItemIds": [],
            }
        )
    )

    assert result["error"] == (
        "coverage sources must use the exact configured source names: flowstate; "
        "do not invent aliases"
    )
    assert pat._store().read()["coverage_receipts"] == []


def test_compact_state_exposes_exact_safety_review_source_ids(monkeypatch, tmp_path):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    manifest = [
        {"id": "flowstate", "inventoryTool": "flowstate_list_tasks", "available": True},
        {"id": "notion-bina-work", "inventoryTool": "notion_data_source_list", "available": True},
    ]
    pat._store().set_task_source_manifest(manifest)

    compact = json.loads(pat._handle_get_state({"mode": "compact"}))["result"]["state"]
    source_description = pat.SAFETY_REVIEW_SCHEMA["parameters"]["properties"]["sources"][
        "description"
    ]

    assert compact["taskSourceManifest"] == manifest
    assert "exact" in source_description.lower()
    assert "taskSourceManifest" in source_description


def test_invalid_safety_review_does_not_partially_register_items(monkeypatch, tmp_path):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    result = json.loads(
        pat._handle_safety_review(
            {
                "cadence": "daily",
                "scopeFingerprint": "flowstate:sequence-42",
                "sources": [{"id": "flowstate", "status": "fresh", "revision": "42"}],
                "protectedItems": [
                    {
                        "id": "flowstate:unsafe",
                        "source": "flowstate",
                        "sourceId": "unsafe",
                        "kind": "commitment",
                        "title": "Unsafe incomplete item",
                        "consequence": "Could be missed",
                        "disposition": "actionable",
                    }
                ],
                "reviewedItemIds": [],
                "riskItemIds": [],
                "unresolvedItemIds": [],
            }
        )
    )
    state = json.loads(pat._handle_get_state({"mode": "full"}))["result"]["state"]

    assert "next action" in result["error"]
    assert state["protectedItems"] == []
    assert state["latestCoverageReceipt"] is None


def test_inventory_reconciliation_returns_exact_counts_only_from_complete_sources():
    import tools.personal_assistant_tool as pat

    result = json.loads(
        pat._handle_reconcile_inventory(
            {
                "inventoryQuestion": "Which tasks still need characterization?",
                "sources": [
                    {
                        "sourceId": "notion:bina-tasks",
                        "scope": "open rows owned by Noam",
                        "capturedAt": "2026-07-14T15:00:00Z",
                        "complete": True,
                        "items": [
                            {
                                "id": "page-1",
                                "title": "First",
                                "classification": "uncharacterized",
                                "evidence": "project is empty",
                            },
                            {
                                "id": "page-2",
                                "title": "Second",
                                "classification": "characterized",
                                "evidence": "project and next action are explicit",
                            },
                        ],
                    }
                ],
            }
        )
    )["result"]

    assert result["verified"] is True
    assert result["exactTotal"] == 2
    assert result["exactUncharacterized"] == 1
    assert result["sources"][0]["observedTotal"] == 2


def test_inventory_reconciliation_refuses_exact_count_for_partial_or_unknown_evidence():
    import tools.personal_assistant_tool as pat

    result = json.loads(
        pat._handle_reconcile_inventory(
            {
                "inventoryQuestion": "How many tasks remain?",
                "sources": [
                    {
                        "sourceId": "obsidian:task-notes",
                        "scope": "task notes found by current search",
                        "capturedAt": "2026-07-14T15:00:00Z",
                        "complete": False,
                        "items": [
                            {
                                "id": "note-1",
                                "title": "Found task",
                                "classification": "unknown",
                                "evidence": "note lacks a stable project field",
                            }
                        ],
                    }
                ],
            }
        )
    )["result"]

    assert result["verified"] is False
    assert result["exactTotal"] is None
    assert result["exactUncharacterized"] is None
    assert result["observedTotal"] == 1
    assert result["blockingReasons"] == [
        "source obsidian:task-notes is partial",
        "1 item has unknown characterization",
    ]


def test_inventory_reconciliation_surfaces_cross_source_conflicts():
    import tools.personal_assistant_tool as pat

    result = json.loads(
        pat._handle_reconcile_inventory(
            {
                "inventoryQuestion": "How many tasks remain?",
                "sources": [
                    {
                        "sourceId": "notion:tasks",
                        "scope": "open tasks",
                        "capturedAt": "2026-07-14T15:00:00Z",
                        "complete": True,
                        "items": [
                            {
                                "id": "page-1",
                                "canonicalId": "task-1",
                                "title": "Task",
                                "classification": "characterized",
                                "evidence": "project exists",
                            }
                        ],
                    },
                    {
                        "sourceId": "obsidian:ledger",
                        "scope": "linked task records",
                        "capturedAt": "2026-07-14T15:01:00Z",
                        "complete": True,
                        "items": [
                            {
                                "id": "note-9",
                                "canonicalId": "task-1",
                                "title": "Task",
                                "classification": "uncharacterized",
                                "evidence": "ledger says project missing",
                            }
                        ],
                    },
                ],
            }
        )
    )["result"]

    assert result["verified"] is False
    assert result["exactTotal"] is None
    assert result["conflicts"][0]["canonicalId"] == "task-1"


def test_tool_registration_is_scoped_to_office_work(monkeypatch, tmp_path):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("default", tmp_path))
    assert pat._check_office_work_profile() is False
    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    assert pat._check_office_work_profile() is True


def test_personal_assistant_can_be_configured_but_is_off_by_default():
    from hermes_cli.tools_config import CONFIGURABLE_TOOLSETS, _DEFAULT_OFF_TOOLSETS

    assert "personal_assistant" in {entry[0] for entry in CONFIGURABLE_TOOLSETS}
    assert "personal_assistant" in _DEFAULT_OFF_TOOLSETS
