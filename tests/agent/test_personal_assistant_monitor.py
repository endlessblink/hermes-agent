from datetime import datetime, timedelta, timezone
import json
import urllib.error

import pytest

from agent.personal_assistant_monitor import (
    ConnectorResponseError,
    ack_candidate_event,
    defer_candidate_event,
    fetch_flowstate_context,
    lease_candidate_event,
    main,
    retry_candidate_event,
    settle_candidate_event,
    run_cli_monitor_check,
    run_monitor_check,
)


NOW = datetime(2026, 7, 12, 22, 0, tzinfo=timezone.utc)


def _inventory_context(
    tasks=None,
    *,
    scope_fingerprint="0123456789abcdef",
    change_sequence=7,
    **context,
):
    tasks = [] if tasks is None else tasks
    return {
        **context,
        "taskInventory": {
            "source": "flowstate",
            "scope": "all open tasks visible to the authenticated user",
            "scopeKind": "personal",
            "scopeFingerprint": scope_fingerprint,
            # The inventory validator requires a live capturedAt (within minutes
            # of wall-clock time), so mint it dynamically instead of freezing it.
            "capturedAt": datetime.now(timezone.utc).isoformat(),
            "appVersion": "1.4.263",
            "fresh": True,
            "complete": True,
            "changeSequence": change_sequence,
            "total": len(tasks),
            "items": tasks,
            "page": {"limit": 100, "nextCursor": None, "hasMore": False},
        },
    }


def _inventory_task(index, **overrides):
    task = {
        "id": f"00000000-0000-4000-8000-{index:012x}",
        "title": f"Task {index}",
        "status": "todo",
        "priority": "medium",
        "canonicalRevision": index + 1,
    }
    task.update(overrides)
    return task


class _ConnectorHttpError(RuntimeError):
    def __init__(self, message, *, code, status):
        super().__init__(message)
        self.code = code
        self.status = status


def test_first_check_establishes_baseline_without_emitting(tmp_path):
    result = run_monitor_check(
        tmp_path,
        {"tasks": [{"id": "t1", "title": "Prepare", "priority": "medium"}]},
        now=NOW,
    )

    assert result == {"status": "checked", "candidate_count": 0}
    assert lease_candidate_event(tmp_path, "gateway", now=NOW) is None


def test_real_flowstate_pressure_keys_are_preserved_and_overdue_increase_emits(tmp_path):
    run_monitor_check(
        tmp_path,
        {
            "taskPressure": {
                "todayCount": 2,
                "overdueCount": 0,
                "noDateCount": 4,
                "highPriorityOpenCount": 1,
            }
        },
        now=NOW,
    )

    result = run_monitor_check(
        tmp_path,
        {
            "taskPressure": {
                "todayCount": 3,
                "overdueCount": 1,
                "noDateCount": 4,
                "highPriorityOpenCount": 1,
            }
        },
        now=NOW + timedelta(minutes=15),
    )

    assert result == {"status": "checked", "candidate_count": 1}
    event = lease_candidate_event(tmp_path, "gateway", now=NOW + timedelta(minutes=16))
    assert event is not None
    assert event["kind"] == "deadline_risk"
    assert event["evidence"] == {"overdue": 1}

    state = json.loads(
        (tmp_path / "state" / "personal-assistant-monitor" / "state.json").read_text()
    )
    assert state["snapshot"]["taskPressure"] == {
        "todayCount": 3,
        "overdueCount": 1,
        "noDateCount": 4,
        "highPriorityOpenCount": 1,
    }


def test_static_high_priority_task_is_surfaced_once_when_baseline_is_created(tmp_path):
    context = {
        "taskPressure": {
            "todayCount": 0,
            "overdueCount": 0,
            "noDateCount": 1,
            "highPriorityOpenCount": 1,
        },
        "tasks": [
            {
                "id": "protected-health-task",
                "title": "Arrange required blood test",
                "priority": "high",
                "status": "todo",
            }
        ],
    }

    first = run_monitor_check(tmp_path, context, now=NOW)
    repeated = run_monitor_check(tmp_path, context, now=NOW + timedelta(minutes=15))

    assert first == {"status": "checked", "candidate_count": 1}
    assert repeated == {"status": "checked", "candidate_count": 0}
    event = lease_candidate_event(tmp_path, "gateway", now=NOW + timedelta(minutes=16))
    assert event is not None
    assert event["kind"] == "changed_high_priority"
    assert event["subject"] == "task:protected-health-task"


def test_new_high_priority_task_is_surfaced_once_without_a_priority_transition(tmp_path):
    run_monitor_check(
        tmp_path,
        {
            "taskPressure": {
                "todayCount": 0,
                "overdueCount": 0,
                "noDateCount": 0,
                "highPriorityOpenCount": 0,
            },
            "tasks": [],
        },
        now=NOW,
    )
    changed = {
        "taskPressure": {
            "todayCount": 0,
            "overdueCount": 0,
            "noDateCount": 1,
            "highPriorityOpenCount": 1,
        },
        "tasks": [
            {
                "id": "new-protected-task",
                "title": "Call the specialist",
                "priority": "high",
                "status": "todo",
            }
        ],
    }

    first = run_monitor_check(tmp_path, changed, now=NOW + timedelta(minutes=15))
    repeated = run_monitor_check(tmp_path, changed, now=NOW + timedelta(minutes=30))

    assert first == {"status": "checked", "candidate_count": 1}
    assert repeated == {"status": "checked", "candidate_count": 0}
    event = lease_candidate_event(tmp_path, "gateway", now=NOW + timedelta(minutes=31))
    assert event is not None
    assert event["kind"] == "changed_high_priority"
    assert event["subject"] == "task:new-protected-task"


def test_material_changes_emit_deduplicated_restart_safe_candidates(tmp_path):
    run_monitor_check(tmp_path, {"tasks": []}, now=NOW)
    changed = {
        "taskPressure": {"overdue": 1},
        "scheduleDriftMinutes": 45,
        "tasks": [
            {"id": "urgent", "title": "Send contract", "priority": "high", "blocked": True}
        ],
    }

    first = run_monitor_check(tmp_path, changed, now=NOW + timedelta(minutes=15))
    repeated = run_monitor_check(tmp_path, changed, now=NOW + timedelta(minutes=30))

    assert first == {"status": "checked", "candidate_count": 4}
    assert repeated == {"status": "checked", "candidate_count": 0}


def test_high_priority_events_cover_static_new_and_promoted_tasks_without_repeating(tmp_path):
    baseline = run_monitor_check(
        tmp_path,
        {
            "tasks": [
                {"id": "promoted", "title": "Promote me", "priority": "medium"},
                {"id": "already-high", "title": "Stable", "priority": "high", "duration": 20},
            ]
        },
        now=NOW,
    )
    assert baseline == {"status": "checked", "candidate_count": 1}
    standing = lease_candidate_event(tmp_path, "gateway", now=NOW + timedelta(minutes=1))
    assert standing is not None
    assert standing["evidence"]["taskId"] == "already-high"
    assert ack_candidate_event(tmp_path, standing["id"], standing["lease_id"])

    result = run_monitor_check(
        tmp_path,
        {
            "tasks": [
                {"id": "promoted", "title": "Promote me", "priority": "high"},
                {"id": "already-high", "title": "Stable", "priority": "high", "duration": 35},
                {"id": "new-high", "title": "Created by this plan", "priority": "high"},
            ]
        },
        now=NOW + timedelta(minutes=15),
    )

    assert result == {"status": "checked", "candidate_count": 2}
    task_ids = set()
    for minute in (16, 17):
        event = lease_candidate_event(tmp_path, "gateway", now=NOW + timedelta(minutes=minute))
        assert event is not None
        assert event["kind"] == "changed_high_priority"
        task_ids.add(event["evidence"]["taskId"])
        assert ack_candidate_event(tmp_path, event["id"], event["lease_id"])
    assert task_ids == {"promoted", "new-high"}
    assert lease_candidate_event(tmp_path, "gateway", now=NOW + timedelta(minutes=18)) is None


def test_more_uncategorized_tasks_emit_one_preview_only_organization_candidate(tmp_path):
    run_monitor_check(
        tmp_path,
        {
            "tasks": [
                {"id": "assigned", "title": "Already filed", "projectId": "project-1"},
                {"id": "existing-loose", "title": "Existing loose task", "projectId": None},
            ]
        },
        now=NOW,
    )

    result = run_monitor_check(
        tmp_path,
        {
            "tasks": [
                {"id": "assigned", "title": "Already filed", "projectId": "project-1"},
                {"id": "existing-loose", "title": "Existing loose task", "projectId": None},
                {"id": "new-loose", "title": "Needs a home", "projectId": None},
            ]
        },
        now=NOW + timedelta(minutes=15),
    )

    assert result == {"status": "checked", "candidate_count": 1}
    event = lease_candidate_event(tmp_path, "gateway", now=NOW + timedelta(minutes=16))
    assert event is not None
    assert event["kind"] == "uncategorized_tasks"
    assert event["subject"] == "flowstate:uncategorized"
    assert event["evidence"] == {
        "count": 2,
        "added": [{"taskId": "new-loose", "title": "Needs a home"}],
        "action": "suggest_preview",
    }


def test_project_metadata_schema_introduction_does_not_emit_uncategorized_backlog(tmp_path):
    run_monitor_check(
        tmp_path,
        {"tasks": [{"id": "existing", "title": "Existing task"}]},
        now=NOW,
    )

    result = run_monitor_check(
        tmp_path,
        {"tasks": [{"id": "existing", "title": "Existing task", "projectId": None}]},
        now=NOW + timedelta(minutes=15),
    )

    assert result == {"status": "checked", "candidate_count": 0}
    assert lease_candidate_event(tmp_path, "gateway", now=NOW + timedelta(minutes=16)) is None


def test_schedule_drift_emits_on_threshold_crossing_or_material_jump_only(tmp_path):
    run_monitor_check(tmp_path, {"scheduleDriftMinutes": 29}, now=NOW)

    crossing = run_monitor_check(
        tmp_path, {"scheduleDriftMinutes": 30}, now=NOW + timedelta(minutes=15)
    )
    noise = run_monitor_check(
        tmp_path, {"scheduleDriftMinutes": 31}, now=NOW + timedelta(minutes=30)
    )
    material = run_monitor_check(
        tmp_path, {"scheduleDriftMinutes": 46}, now=NOW + timedelta(minutes=45)
    )

    assert crossing["candidate_count"] == 1
    assert noise["candidate_count"] == 0
    assert material["candidate_count"] == 1


def test_event_identity_counts_occurrences_per_subject_not_globally(tmp_path):
    both = tmp_path / "both"
    single = tmp_path / "single"
    baseline = {
        "tasks": [
            {"id": "task-a", "title": "A", "priority": "medium"},
            {"id": "task-b", "title": "B", "priority": "medium"},
        ]
    }
    promoted = {
        "tasks": [
            {"id": "task-a", "title": "A", "priority": "high"},
            {"id": "task-b", "title": "B", "priority": "high"},
        ]
    }
    run_monitor_check(both, baseline, now=NOW)
    run_monitor_check(both, promoted, now=NOW + timedelta(minutes=15))

    first = lease_candidate_event(both, "gateway", now=NOW + timedelta(minutes=16))
    assert first is not None
    assert ack_candidate_event(both, first["id"], first["lease_id"])
    second = lease_candidate_event(both, "gateway", now=NOW + timedelta(minutes=17))
    assert second is not None

    run_monitor_check(
        single,
        {"tasks": [{"id": "task-b", "title": "B", "priority": "medium"}]},
        now=NOW,
    )
    run_monitor_check(
        single,
        {"tasks": [{"id": "task-b", "title": "B", "priority": "high"}]},
        now=NOW + timedelta(minutes=15),
    )
    task_b_alone = lease_candidate_event(single, "gateway", now=NOW + timedelta(minutes=16))

    assert first["occurrence"] == second["occurrence"] == 1
    assert task_b_alone is not None
    assert second["id"] == task_b_alone["id"]


def test_queue_lease_survives_restart_and_ack_is_idempotent(tmp_path):
    run_monitor_check(tmp_path, {"tasks": []}, now=NOW)
    run_monitor_check(
        tmp_path,
        {"taskPressure": {"overdue": 1}, "tasks": []},
        now=NOW + timedelta(minutes=15),
    )

    leased = lease_candidate_event(tmp_path, "gateway", now=NOW + timedelta(minutes=16))
    assert leased and leased["kind"] == "deadline_risk"
    assert lease_candidate_event(tmp_path, "other", now=NOW + timedelta(minutes=16)) is None
    assert ack_candidate_event(tmp_path, leased["id"], leased["lease_id"]) is True
    assert ack_candidate_event(tmp_path, leased["id"], leased["lease_id"]) is True
    assert lease_candidate_event(tmp_path, "gateway", now=NOW + timedelta(minutes=17)) is None


def test_v2_event_can_be_settled_with_an_explicit_terminal_disposition(tmp_path):
    run_monitor_check(tmp_path, {"taskPressure": {"overdue": 0}}, now=NOW)
    run_monitor_check(
        tmp_path,
        {"taskPressure": {"overdue": 1}},
        now=NOW + timedelta(minutes=15),
    )
    leased = lease_candidate_event(tmp_path, "gateway", now=NOW + timedelta(minutes=16))

    assert leased is not None
    assert leased["version"] == 1
    assert leased["lifecycle_version"] == 2
    assert leased["status"] == "leased"
    assert leased["attempts"] == 1
    assert not settle_candidate_event(tmp_path, leased["id"], "another-lease", "merged")
    assert settle_candidate_event(tmp_path, leased["id"], leased["lease_id"], "merged")
    assert settle_candidate_event(tmp_path, leased["id"], leased["lease_id"], "merged")
    assert lease_candidate_event(tmp_path, "gateway", now=NOW + timedelta(minutes=17)) is None

    queue_path = tmp_path / "state" / "personal-assistant-monitor" / "queue.json"
    stored = json.loads(queue_path.read_text(encoding="utf-8"))["events"][0]
    assert stored["status"] == "merged"
    assert stored["disposition"] == "merged"
    assert stored["lease"] is None


def test_busy_turn_deferral_does_not_spend_delivery_attempts(tmp_path):
    run_monitor_check(tmp_path, {"taskPressure": {"overdue": 0}}, now=NOW)
    run_monitor_check(
        tmp_path,
        {"taskPressure": {"overdue": 1}},
        now=NOW + timedelta(minutes=15),
    )
    leased = lease_candidate_event(tmp_path, "gateway", now=NOW + timedelta(minutes=16))

    assert leased is not None and leased["attempts"] == 1
    assert defer_candidate_event(
        tmp_path,
        leased["id"],
        leased["lease_id"],
        now=NOW + timedelta(minutes=16),
        delay=timedelta(seconds=5),
    )
    assert lease_candidate_event(
        tmp_path, "gateway", now=NOW + timedelta(minutes=16, seconds=4)
    ) is None
    again = lease_candidate_event(
        tmp_path, "gateway", now=NOW + timedelta(minutes=16, seconds=5)
    )
    assert again is not None and again["attempts"] == 1


def test_failed_delivery_retries_after_backoff_then_dead_letters_without_raw_error(tmp_path):
    run_monitor_check(tmp_path, {"taskPressure": {"overdue": 0}}, now=NOW)
    run_monitor_check(
        tmp_path,
        {"taskPressure": {"overdue": 1}},
        now=NOW + timedelta(minutes=15),
    )
    first = lease_candidate_event(tmp_path, "gateway", now=NOW + timedelta(minutes=16))
    assert first is not None
    assert not retry_candidate_event(
        tmp_path,
        first["id"],
        "another-lease",
        RuntimeError("not persisted"),
        now=NOW + timedelta(minutes=16),
    )
    assert retry_candidate_event(
        tmp_path,
        first["id"],
        first["lease_id"],
        RuntimeError("Bearer super-secret-value failed"),
        now=NOW + timedelta(minutes=16),
        backoff=timedelta(minutes=2),
    )
    assert lease_candidate_event(tmp_path, "gateway", now=NOW + timedelta(minutes=17)) is None

    second = lease_candidate_event(tmp_path, "gateway", now=NOW + timedelta(minutes=18))
    assert second is not None and second["attempts"] == 2
    assert retry_candidate_event(
        tmp_path,
        second["id"],
        second["lease_id"],
        {"category": "submission_failed", "code": "gateway_error"},
        now=NOW + timedelta(minutes=18),
        backoff=timedelta(0),
    )
    third = lease_candidate_event(tmp_path, "gateway", now=NOW + timedelta(minutes=18))
    assert third is not None and third["attempts"] == 3
    assert retry_candidate_event(
        tmp_path,
        third["id"],
        third["lease_id"],
        RuntimeError("Bearer super-secret-value failed again"),
        now=NOW + timedelta(minutes=18),
    )

    queue_path = tmp_path / "state" / "personal-assistant-monitor" / "queue.json"
    stored_text = queue_path.read_text(encoding="utf-8")
    stored = json.loads(stored_text)["events"][0]
    assert stored["status"] == "dead_letter"
    assert stored["disposition"] == "dead_letter"
    assert "super-secret-value" not in stored_text
    assert lease_candidate_event(tmp_path, "gateway", now=NOW + timedelta(hours=1)) is None
    health_text = (tmp_path / "logs" / "personal-assistant-monitor.jsonl").read_text(
        encoding="utf-8"
    )
    assert '"event":"dead_letter"' in health_text
    assert "event-" not in health_text


def test_repeated_expired_leases_dead_letter_after_the_bounded_attempt_limit(tmp_path):
    run_monitor_check(tmp_path, {"taskPressure": {"overdue": 0}}, now=NOW)
    run_monitor_check(
        tmp_path,
        {"taskPressure": {"overdue": 1}},
        now=NOW + timedelta(minutes=15),
    )
    for second in range(3):
        leased = lease_candidate_event(
            tmp_path,
            "crashing-gateway",
            now=NOW + timedelta(minutes=16, seconds=second),
            lease_ttl=timedelta(0),
        )
        assert leased is not None and leased["attempts"] == second + 1

    assert (
        lease_candidate_event(
            tmp_path,
            "gateway",
            now=NOW + timedelta(minutes=16, seconds=3),
        )
        is None
    )
    queue_path = tmp_path / "state" / "personal-assistant-monitor" / "queue.json"
    stored = json.loads(queue_path.read_text(encoding="utf-8"))["events"][0]
    assert stored["status"] == "dead_letter"
    assert stored["last_error"]["code"] == "lease_expired"
    health = (tmp_path / "logs" / "personal-assistant-monitor.jsonl").read_text(
        encoding="utf-8"
    )
    assert '"event":"dead_letter"' in health


def test_v1_acked_and_pending_queue_entries_migrate_without_redelivery_or_loss(tmp_path):
    queue_path = tmp_path / "state" / "personal-assistant-monitor" / "queue.json"
    queue_path.parent.mkdir(parents=True)
    queue_path.write_text(
        json.dumps(
            {
                "version": 1,
                "events": [
                    {
                        "id": "old-done",
                        "version": 1,
                        "kind": "deadline_risk",
                        "occurrence": 1,
                        "evidence": {"overdue": 1},
                        "created_at": NOW.isoformat(),
                        "lease": None,
                        "acked": True,
                    },
                    {
                        "id": "old-pending",
                        "version": 1,
                        "kind": "blocker",
                        "occurrence": 1,
                        "evidence": {"taskId": "task-1"},
                        "created_at": NOW.isoformat(),
                        "lease": None,
                        "acked": False,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    leased = lease_candidate_event(tmp_path, "gateway", now=NOW + timedelta(minutes=1))

    assert leased is not None and leased["id"] == "old-pending"
    migrated = json.loads(queue_path.read_text(encoding="utf-8"))
    assert migrated["version"] == 2
    by_id = {event["id"]: event for event in migrated["events"]}
    assert by_id["old-done"]["status"] == "handled"
    assert by_id["old-pending"]["status"] == "leased"
    assert by_id["old-pending"]["subject"] == "task:task-1"


def test_offline_check_fails_closed_and_does_not_destroy_baseline(tmp_path):
    run_monitor_check(tmp_path, {"tasks": []}, now=NOW)

    result = run_monitor_check(tmp_path, None, now=NOW + timedelta(minutes=15))

    assert result == {"status": "offline", "candidate_count": 0}
    assert lease_candidate_event(tmp_path, "gateway", now=NOW) is None


def test_complete_inventory_snapshot_keeps_every_id_revision_and_only_bounded_fields(tmp_path):
    # run_monitor_check snapshots the fetch-shaped context: the bounded inventory
    # metadata lives under snapshot["inventory"] and every task is projected to
    # the metadata allowlist. Feed that shape directly (fetch_flowstate_context
    # produces it in production).
    tasks = [
        {"id": f"00000000-0000-4000-8000-{index:012x}", "title": f"Task {index}",
         "canonicalRevision": index + 1}
        for index in range(125)
    ]
    tasks[-1].update({"blocker": True, "duration": 90, "outside_allowlist": "x" * 500})
    context = {
        "tasks": tasks,
        "inventory": {
            "scope": "all open tasks visible to the authenticated user",
            "scopeKind": "personal",
            "scopeFingerprint": "0123456789abcdef",
            "capturedAt": datetime.now(timezone.utc).isoformat(),
            "appVersion": "1.4.263",
            "complete": True,
            "fresh": True,
            "total": 125,
            "changeSequence": 7,
        },
    }

    result = run_monitor_check(tmp_path, context, now=NOW)

    assert result == {"status": "checked", "candidate_count": 0}
    state = json.loads(
        (tmp_path / "state" / "personal-assistant-monitor" / "state.json").read_text()
    )
    snapshot = state["snapshot"]
    assert snapshot["inventory"]["scopeFingerprint"] == "0123456789abcdef"
    assert snapshot["inventory"]["changeSequence"] == 7
    assert snapshot["inventory"]["total"] == 125
    assert len(snapshot["tasks"]) == 125
    assert snapshot["tasks"][-1]["canonicalRevision"] == 125
    assert snapshot["tasks"][-1]["blocker"] is True
    assert snapshot["tasks"][-1]["duration"] == 90
    # Fields outside the metadata allowlist are dropped from the snapshot.
    assert "outside_allowlist" not in snapshot["tasks"][-1]


def test_inventory_scope_change_resets_baseline_without_cross_scope_events(tmp_path):
    run_monitor_check(
        tmp_path,
        _inventory_context([_inventory_task(1)], scope_fingerprint="1111111111111111"),
        now=NOW,
    )

    result = run_monitor_check(
        tmp_path,
        _inventory_context(
            [_inventory_task(2, priority="high", blocked=True)],
            scope_fingerprint="2222222222222222",
            change_sequence=1,
        ),
        now=NOW + timedelta(minutes=15),
    )

    assert result == {"status": "checked", "candidate_count": 0}
    assert lease_candidate_event(tmp_path, "gateway", now=NOW + timedelta(minutes=16)) is None


def test_resolved_then_recurring_risk_gets_a_new_occurrence_id(tmp_path):
    run_monitor_check(tmp_path, {"taskPressure": {"overdue": 0}}, now=NOW)
    run_monitor_check(
        tmp_path,
        {"taskPressure": {"overdue": 1}},
        now=NOW + timedelta(minutes=15),
    )
    first = lease_candidate_event(tmp_path, "gateway", now=NOW + timedelta(minutes=16))
    assert first is not None
    assert ack_candidate_event(tmp_path, first["id"], first["lease_id"])

    run_monitor_check(
        tmp_path,
        {"taskPressure": {"overdue": 0}},
        now=NOW + timedelta(minutes=30),
    )
    run_monitor_check(
        tmp_path,
        {"taskPressure": {"overdue": 1}},
        now=NOW + timedelta(minutes=45),
    )
    recurring = lease_candidate_event(tmp_path, "gateway", now=NOW + timedelta(minutes=46))

    assert recurring is not None
    assert recurring["id"] != first["id"]
    assert recurring["occurrence"] == 2


def test_acknowledged_history_is_bounded_without_dropping_pending_events(tmp_path):
    run_monitor_check(tmp_path, {"taskPressure": {"overdue": 0}}, now=NOW)
    last = None
    for occurrence in range(130):
        offset = occurrence * 2 + 1
        run_monitor_check(
            tmp_path,
            {"taskPressure": {"overdue": 1}},
            now=NOW + timedelta(minutes=offset),
        )
        last = lease_candidate_event(
            tmp_path,
            "gateway",
            now=NOW + timedelta(minutes=offset, seconds=1),
        )
        assert last is not None
        assert ack_candidate_event(tmp_path, last["id"], last["lease_id"])
        run_monitor_check(
            tmp_path,
            {"taskPressure": {"overdue": 0}},
            now=NOW + timedelta(minutes=offset + 1),
        )

    queue_path = tmp_path / "state" / "personal-assistant-monitor" / "queue.json"
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    assert len(queue["events"]) == 128
    assert all(event["acked"] for event in queue["events"])
    assert ack_candidate_event(tmp_path, last["id"], last["lease_id"]) is True


def test_scheduled_assessment_enqueues_once_per_jerusalem_date_after_nine(tmp_path):
    before_nine = datetime(2026, 7, 12, 5, 59, tzinfo=timezone.utc)
    at_nine = datetime(2026, 7, 12, 6, 0, tzinfo=timezone.utc)

    before = run_monitor_check(tmp_path, {"tasks": []}, now=before_nine)
    first = run_monitor_check(tmp_path, {"tasks": []}, now=at_nine)
    repeat = run_monitor_check(tmp_path, {"tasks": []}, now=at_nine + timedelta(hours=1))

    assert before["candidate_count"] == 0
    assert first["candidate_count"] == 1
    assert repeat["candidate_count"] == 0
    event = lease_candidate_event(tmp_path, "gateway", now=at_nine)
    assert event and event["kind"] == "scheduled_assessment"
    assert event["evidence"] == {"localDate": "2026-07-12"}


def test_cli_notifies_only_when_new_candidates_are_added(tmp_path):
    notices = []
    notify = lambda title, body: notices.append((title, body))

    run_cli_monitor_check(tmp_path, {"tasks": []}, notifier=notify, now=NOW)
    run_cli_monitor_check(
        tmp_path,
        {"taskPressure": {"overdue": 1}, "tasks": []},
        notifier=notify,
        now=NOW + timedelta(minutes=15),
    )
    run_cli_monitor_check(
        tmp_path,
        {"taskPressure": {"overdue": 1}, "tasks": []},
        notifier=notify,
        now=NOW + timedelta(minutes=30),
    )
    run_cli_monitor_check(tmp_path, None, notifier=notify, now=NOW + timedelta(minutes=45))

    assert notices == [
        (
            "Hermes personal assistant",
            "Personal assistant noticed a material change and will prepare it in Hermes.",
        )
    ]


def test_fetch_flowstate_context_uses_one_testable_seam_and_validates_shapes():
    seen = []
    inventory = _inventory_context([_inventory_task(1)])["taskInventory"]

    def request(method, path, **kwargs):
        seen.append((method, path, kwargs))
        if path == "/api/assistant/context":
            return {"taskPressure": {"overdue": 1}}
        return inventory

    result = fetch_flowstate_context(request)

    # fetch_flowstate_context returns the assistant context merged with the
    # exact task items and a bounded inventory summary.
    assert result["taskPressure"] == {"overdue": 1}
    assert result["tasks"] == inventory["items"]
    assert result["inventory"]["scopeFingerprint"] == inventory["scopeFingerprint"]
    assert result["inventory"]["total"] == inventory["total"]
    assert result["inventory"]["complete"] is True
    assert seen == [
        ("GET", "/api/assistant/context", {"allow_stale_cache": False}),
        ("GET", "/api/tasks/inventory", {"allow_stale_cache": False}),
    ]


def test_fetch_flowstate_context_rejects_incomplete_inventory():
    from tools.flowstate_tool import _FlowStateApiError

    inventory = _inventory_context([_inventory_task(1)])["taskInventory"]
    inventory.update({"complete": False, "page": {"limit": 100, "nextCursor": "x", "hasMore": True}})
    inventory.pop("total")
    inventory.pop("changeSequence")

    def request(method, path, **kwargs):
        if path == "/api/assistant/context":
            return {}
        return inventory

    # An incomplete inventory validates structurally but is refused for monitor
    # comparison with a typed invalid_inventory_receipt error.
    with pytest.raises(_FlowStateApiError) as caught:
        fetch_flowstate_context(request)

    assert caught.value.code == "invalid_inventory_receipt"


@pytest.mark.parametrize(
    ("failure", "category", "code"),
    [
        (
            _ConnectorHttpError("Bearer auth-secret", code="unauthorized", status=401),
            "authentication",
            "unauthorized",
        ),
        (
            _ConnectorHttpError("Bearer signin-secret", code="not_signed_in", status=503),
            "not_signed_in",
            "not_signed_in",
        ),
        (
            _ConnectorHttpError("Bearer signout-secret", code="signed_out", status=503),
            "signed_out",
            "signed_out",
        ),
        (TimeoutError("Bearer timeout-secret"), "timeout", "timeout"),
        (
            urllib.error.URLError(ConnectionRefusedError("Bearer refused-secret")),
            "connection_refused",
            "connection_refused",
        ),
        (json.JSONDecodeError("Bearer invalid-secret", "x", 0), "invalid_response", "invalid_json"),
    ],
)
def test_cli_connector_failures_are_typed_persisted_redacted_and_nonzero(
    tmp_path, capsys, failure, category, code
):
    profile_home = tmp_path / category

    def fail():
        raise failure

    exit_code = main(
        ["--profile-home", str(profile_home)],
        fetch_context=fail,
    )

    assert exit_code == 75
    state_path = profile_home / "state" / "personal-assistant-monitor" / "state.json"
    stored_text = state_path.read_text(encoding="utf-8")
    stored = json.loads(stored_text)
    assert stored["last_status"] == "offline"
    assert stored["connector_error"]["category"] == category
    assert stored["connector_error"]["code"] == code
    combined = stored_text + capsys.readouterr().err
    assert "timeout-secret" not in combined
    assert "refused-secret" not in combined
    assert "invalid-secret" not in combined
    assert "auth-secret" not in combined
    assert "signin-secret" not in combined
    assert "signout-secret" not in combined
    health_text = (
        profile_home / "logs" / "personal-assistant-monitor.jsonl"
    ).read_text(encoding="utf-8")
    health = json.loads(health_text.splitlines()[-1])
    assert health == {
        "component": "personal_assistant_monitor",
        "count": 0,
        "event": "connector_failure",
        "source": "producer",
        "status": category,
        "ts": health["ts"],
    }
    assert "secret" not in health_text


def test_cli_unexpected_monitor_crash_keeps_failure_exit(tmp_path):
    def crash():
        raise RuntimeError("unexpected monitor bug")

    assert main(["--profile-home", str(tmp_path)], fetch_context=crash) == 1
