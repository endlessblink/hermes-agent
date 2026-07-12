from datetime import datetime, timedelta, timezone
import json

from agent.personal_assistant_monitor import (
    ack_candidate_event,
    lease_candidate_event,
    run_cli_monitor_check,
    run_monitor_check,
)


NOW = datetime(2026, 7, 12, 22, 0, tzinfo=timezone.utc)


def test_first_check_establishes_baseline_without_emitting(tmp_path):
    result = run_monitor_check(
        tmp_path,
        {"tasks": [{"id": "t1", "title": "Prepare", "priority": "high"}]},
        now=NOW,
    )

    assert result == {"status": "checked", "candidate_count": 0}
    assert lease_candidate_event(tmp_path, "gateway", now=NOW) is None


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


def test_offline_check_fails_closed_and_does_not_destroy_baseline(tmp_path):
    run_monitor_check(tmp_path, {"tasks": []}, now=NOW)

    result = run_monitor_check(tmp_path, None, now=NOW + timedelta(minutes=15))

    assert result == {"status": "offline", "candidate_count": 0}
    assert lease_candidate_event(tmp_path, "gateway", now=NOW) is None


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
