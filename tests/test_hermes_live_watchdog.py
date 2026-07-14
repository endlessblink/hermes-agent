import importlib.util
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from tui_gateway import server


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hermes_live_watchdog.py"
SPEC = importlib.util.spec_from_file_location("hermes_live_watchdog", SCRIPT)
watchdog = importlib.util.module_from_spec(SPEC)
assert SPEC is not None
assert SPEC.loader is not None
sys.modules[SPEC.name] = watchdog
SPEC.loader.exec_module(watchdog)


def test_default_monitor_stale_thresholds_cover_the_fifteen_minute_timer_cadence():
    # The monitor timer runs every 15 minutes with up to one minute of systemd
    # accuracy drift. Defaults must include additional scheduling grace or a
    # healthy consumer will be reported stale between successful runs.
    minimum_with_grace = 20 * 60
    assert watchdog.DEFAULT_MONITOR_PRODUCER_STALE_SECONDS >= minimum_with_grace
    assert watchdog.DEFAULT_MONITOR_CONSUMER_STALE_SECONDS >= minimum_with_grace


def test_monitor_stale_alerts_do_not_repeat_faster_than_the_stale_window():
    args = watchdog.parse_args([])

    assert watchdog.monitor_stale_alert_cooldown(args, "producer") >= (
        args.monitor_producer_stale_seconds
    )
    assert watchdog.monitor_stale_alert_cooldown(args, "consumer") >= (
        args.monitor_consumer_stale_seconds
    )


def test_desktop_notification_flag_is_a_compatibility_noop():
    assert watchdog.parse_args([]).notify is False
    assert watchdog.parse_args(["--notify"]).notify is False


def test_idle_session_info_clears_stale_turn_state():
    assert watchdog.is_terminal({"event": "session.info", "running": False}) is True
    assert watchdog.is_terminal({"event": "session.info", "running": True}) is False


def test_discover_ledgers_includes_profile_ledgers(tmp_path):
    home = tmp_path / ".hermes"
    profile_ledger = home / "profiles" / "film-maker" / "logs" / "turn-watchdog.jsonl"
    profile_ledger.parent.mkdir(parents=True)
    profile_ledger.write_text("", encoding="utf-8")

    assert watchdog.discover_ledgers(home) == [
        home / "logs" / "turn-watchdog.jsonl",
        profile_ledger,
    ]


def test_discover_sources_also_tails_desktop_diagnostics(tmp_path):
    home = tmp_path / ".hermes"

    assert watchdog.discover_sources(home) == [
        home / "logs" / "desktop-events.jsonl",
        home / "logs" / "personal-assistant-monitor.jsonl",
        home / "logs" / "turn-watchdog.jsonl",
    ]


def test_discover_sources_includes_profile_monitor_health(tmp_path):
    home = tmp_path / ".hermes"
    health = home / "profiles" / "office-work" / "logs" / "personal-assistant-monitor.jsonl"
    health.parent.mkdir(parents=True)
    health.write_text("", encoding="utf-8")

    assert health in watchdog.discover_sources(home)


def test_discover_sources_includes_expected_monitor_before_its_first_heartbeat(tmp_path):
    home = tmp_path / ".hermes"
    expected = home / "profiles" / "office-work" / "logs" / "personal-assistant-monitor.jsonl"

    assert expected in watchdog.discover_sources(home, monitor_profile="office-work")


def test_missing_expected_monitor_alerts_after_startup_grace(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    ticks = iter([1000.0, 1002.0])
    monkeypatch.setattr(watchdog.time, "time", lambda: next(ticks))
    home = tmp_path / ".hermes"

    args = watchdog.parse_args(
        [
            "--home",
            str(home),
            "--once",
            "--monitor-profile",
            "office-work",
            "--monitor-producer-stale-seconds",
            "1",
            "--monitor-consumer-stale-seconds",
            "1",
        ]
    )
    assert watchdog.run(args) == 0

    alerts = home / "logs" / "live-watchdog-alerts.jsonl"
    emitted = [json.loads(line) for line in alerts.read_text(encoding="utf-8").splitlines()]
    stale = {
        row["event"]: row
        for row in emitted
        if row["event"].startswith("personal_assistant_monitor_")
    }
    assert set(stale) == {
        "personal_assistant_monitor_consumer_stale",
        "personal_assistant_monitor_producer_stale",
    }
    assert all(row["heartbeat_seen"] is False for row in stale.values())


def test_monitor_connector_failure_alert_is_privacy_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    health = home / "profiles" / "office-work" / "logs" / "personal-assistant-monitor.jsonl"
    health.parent.mkdir(parents=True)
    health.write_text(
        json.dumps(
            {
                "ts": "2026-07-13T20:00:00+00:00",
                "component": "personal_assistant_monitor",
                "source": "producer",
                "event": "connector_failure",
                "status": "not_signed_in",
                "count": 0,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    args = watchdog.parse_args(["--home", str(home), "--from-start", "--once"])
    assert watchdog.run(args) == 0

    alerts = home / "logs" / "live-watchdog-alerts.jsonl"
    emitted = [json.loads(line) for line in alerts.read_text(encoding="utf-8").splitlines()]
    alert = next(
        row for row in emitted if row["event"] == "personal_assistant_monitor_connector_failure"
    )
    assert alert["status"] == "not_signed_in"
    serialized = json.dumps(alert)
    assert "evidence" not in serialized
    assert "taskId" not in serialized


def test_flowstate_recovery_requires_sign_in_instead_of_restarting():
    decision = watchdog.classify_flowstate_recovery(
        status="not_signed_in",
        health_ok=False,
        config={"enabled": True, "port": 5577, "token": "secret"},
        app_running=False,
    )

    assert decision == {
        "action": "none",
        "outcome": "auth_required",
        "reason": "flowstate_sign_in_required",
    }


def test_restart_replay_diagnostic_becomes_a_supervisor_incident(tmp_path):
    alert = watchdog.build_incident_alert(
        {
            "session_id": "runtime-private",
            "event": "diagnostic.event",
            "payload": {
                "component": "turn",
                "event": "orphan_recovery_started",
                "details": {"user_ordinal": 4},
            },
        },
        tmp_path / "turn-watchdog.jsonl",
    )

    assert alert["event"] == "restart_interrupted_turn_replayed"
    assert alert["user_ordinal"] == 4
    assert "text" not in json.dumps(alert)


def test_restart_replay_incident_logs_without_requiring_a_session_key(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    ledger = home / "logs" / "turn-watchdog.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps(
            {
                "event": "diagnostic.event",
                "monotonic": time.time(),
                "payload": {
                    "component": "turn",
                    "event": "orphan_recovery_started",
                    "details": {"user_ordinal": 4},
                },
                "session_id": "runtime-private",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    args = watchdog.parse_args(["--home", str(home), "--from-start", "--once"])

    assert watchdog.run(args) == 0
    output = capsys.readouterr().out
    assert "RESTART_INTERRUPTED_TURN_REPLAYED" in output
    assert "SESSION_NOT_FOUND" not in output


def test_idle_timeout_diagnostic_becomes_an_internal_watchdog_incident(tmp_path):
    alert = watchdog.build_incident_alert(
        {
            "session_id": "runtime-private",
            "event": "diagnostic.event",
            "payload": {
                "component": "turn",
                "event": "idle_timeout",
                "details": {"timeout_seconds": 60, "last_progress_event": "tool.complete"},
            },
        },
        tmp_path / "turn-watchdog.jsonl",
    )

    assert alert["event"] == "stuck_turn_automatically_stopped"
    assert alert["timeout_seconds"] == 60
    assert "session_id" not in alert


def test_turn_timeout_is_recorded_as_contained_not_repaired(tmp_path):
    home = tmp_path / ".hermes"

    watchdog._record_turn_timeout_recovery_event(home, "office-work")

    inbox = (
        home
        / "profiles"
        / "office-work"
        / "state"
        / "improvement-supervisor"
        / "runtime-events.jsonl"
    )
    event = json.loads(inbox.read_text(encoding="utf-8"))
    assert event["action"] == "interrupt"
    assert event["outcome"] == "contained"
    assert event["reason"] == "turn_idle_timeout"


def test_compression_timeout_becomes_an_internal_watchdog_incident(tmp_path):
    alert = watchdog.build_incident_alert(
        {
            "session_id": "runtime-private",
            "event": "diagnostic.event",
            "payload": {
                "component": "compression",
                "event": "timeout",
                "details": {"timeout_seconds": 12},
            },
        },
        tmp_path / "turn-watchdog.jsonl",
    )

    assert alert["event"] == "stuck_turn_automatically_stopped"
    assert alert["recovery_reason"] == "compression_timeout"
    assert "session_id" not in alert


def test_flowstate_recovery_launches_absent_app_when_api_is_enabled():
    decision = watchdog.classify_flowstate_recovery(
        status="unavailable",
        health_ok=False,
        config={"enabled": True, "port": 5577, "token": "secret"},
        app_running=False,
    )

    assert decision == {
        "action": "launch",
        "outcome": "repair_started",
        "reason": "flowstate_app_absent",
    }


def test_flowstate_recovery_fails_closed_for_running_but_unhealthy_app():
    decision = watchdog.classify_flowstate_recovery(
        status="unavailable",
        health_ok=False,
        config={"enabled": True, "port": 5577, "token": "secret"},
        app_running=True,
    )

    assert decision["action"] == "none"
    assert decision["outcome"] == "manual_required"


def test_successful_flowstate_launch_is_verified_and_emits_safe_improvement_event(
    tmp_path, monkeypatch
):
    home = tmp_path / ".hermes"
    config_path = tmp_path / "local-api.json"
    config_path.write_text(
        json.dumps({"enabled": True, "port": 5577, "token": "do-not-persist"}),
        encoding="utf-8",
    )
    health = iter([False, False, True])
    launched = []
    monkeypatch.setattr(watchdog, "flowstate_config_path", lambda: config_path)
    monkeypatch.setattr(watchdog, "flowstate_health_ok", lambda _port: next(health))
    monkeypatch.setattr(watchdog, "flowstate_app_running", lambda: False)
    monkeypatch.setattr(watchdog, "launch_flowstate_app", lambda: launched.append(True) or True)
    monkeypatch.setattr(watchdog.time, "sleep", lambda _seconds: None)

    result = watchdog.attempt_flowstate_recovery(
        home=home,
        profile="office-work",
        status="unavailable",
        verify_attempts=3,
    )

    assert launched == [True]
    assert result["outcome"] == "repaired"
    events = home / "profiles" / "office-work" / "state" / "improvement-supervisor" / "runtime-events.jsonl"
    persisted = events.read_text(encoding="utf-8")
    assert "flowstate_connector_recovery" in persisted
    assert "do-not-persist" not in persisted


def test_stale_monitor_consumer_heartbeat_alerts(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    health = home / "profiles" / "office-work" / "logs" / "personal-assistant-monitor.jsonl"
    health.parent.mkdir(parents=True)
    old = datetime.fromtimestamp(time.time() - 60, timezone.utc).isoformat()
    health.write_text(
        json.dumps(
            {
                "ts": old,
                "component": "personal_assistant_monitor",
                "source": "consumer",
                "event": "consumer_heartbeat",
                "status": "available",
                "count": 0,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    args = watchdog.parse_args(
        [
            "--home",
            str(home),
            "--from-start",
            "--once",
            "--monitor-consumer-stale-seconds",
            "1",
        ]
    )
    assert watchdog.run(args) == 0

    alerts = home / "logs" / "live-watchdog-alerts.jsonl"
    emitted = [json.loads(line) for line in alerts.read_text(encoding="utf-8").splitlines()]
    alert = next(
        row for row in emitted if row["event"] == "personal_assistant_monitor_consumer_stale"
    )
    assert alert["source"] == "consumer"
    assert alert["age_seconds"] >= 59


def test_from_end_seeds_existing_monitor_heartbeats_without_replaying_incidents(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    health = home / "profiles" / "office-work" / "logs" / "personal-assistant-monitor.jsonl"
    health.parent.mkdir(parents=True)
    old = datetime.fromtimestamp(time.time() - 60, timezone.utc).isoformat()
    health.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": old,
                        "component": "personal_assistant_monitor",
                        "source": "producer",
                        "event": "producer_heartbeat",
                        "status": "available",
                        "count": 0,
                    }
                ),
                json.dumps(
                    {
                        "ts": old,
                        "component": "personal_assistant_monitor",
                        "source": "producer",
                        "event": "connector_failure",
                        "status": "timeout",
                        "count": 0,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    args = watchdog.parse_args(
        [
            "--home",
            str(home),
            "--once",
            "--monitor-producer-stale-seconds",
            "1",
            "--monitor-consumer-stale-seconds",
            "1",
        ]
    )
    assert args.from_end is True
    assert watchdog.run(args) == 0

    alerts = home / "logs" / "live-watchdog-alerts.jsonl"
    events = [json.loads(line)["event"] for line in alerts.read_text(encoding="utf-8").splitlines()]
    assert "personal_assistant_monitor_producer_stale" in events
    assert "personal_assistant_monitor_consumer_stale" in events
    assert "personal_assistant_monitor_connector_failure" not in events


def test_hidden_sidebar_sessions_are_alerted_immediately_without_private_data(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    diagnostics = home / "logs" / "desktop-events.jsonl"
    diagnostics.parent.mkdir(parents=True)
    diagnostics.write_text(
        json.dumps(
            {
                "ts": "2026-07-13T19:00:00.000Z",
                "severity": "error",
                "component": "sidebar",
                "event": "project_overview_hidden_sessions",
                "message": "Project overview omitted loaded loose sessions",
                "details": {"hidden_count": 2, "presentation": "projects"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    args = watchdog.parse_args(["--home", str(home), "--from-start", "--once"])
    assert watchdog.run(args) == 0

    alerts = home / "logs" / "live-watchdog-alerts.jsonl"
    emitted = [json.loads(line) for line in alerts.read_text(encoding="utf-8").splitlines()]
    alert = next(row for row in emitted if row["event"] == "sidebar_sessions_hidden")
    assert alert["hidden_count"] == 2
    assert alert["ledger"] == str(diagnostics)
    serialized = json.dumps(alert)
    assert "title" not in serialized
    assert "session_id" not in serialized
    assert "private" not in serialized


def test_hidden_sidebar_session_diagnostics_are_deduplicated(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    diagnostics = home / "logs" / "desktop-events.jsonl"
    diagnostics.parent.mkdir(parents=True)
    row = {
        "component": "sidebar",
        "event": "project_overview_hidden_sessions",
        "details": {"hidden_count": 1, "presentation": "projects"},
    }
    diagnostics.write_text("".join(json.dumps(row) + "\n" for _ in range(2)), encoding="utf-8")

    args = watchdog.parse_args(["--home", str(home), "--from-start", "--once"])
    assert watchdog.run(args) == 0

    alerts = home / "logs" / "live-watchdog-alerts.jsonl"
    events = [json.loads(line)["event"] for line in alerts.read_text(encoding="utf-8").splitlines()]
    assert events.count("sidebar_sessions_hidden") == 1


def test_profile_ledger_approval_wait_never_writes_stuck_alert(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    ledger = home / "profiles" / "film-maker" / "logs" / "turn-watchdog.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps(
            {
                "monotonic": time.time() - 10,
                "event": "approval.request",
                "session_id": "sid-profile",
                "session_key": "key-profile",
                "cwd": "/tmp/project",
                "running": True,
                "turn_started_at": time.time() - 20,
                "turn_last_progress_at": time.time() - 10,
                "turn_last_progress_event": "approval.request",
                "payload": {},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    args = watchdog.parse_args(
        [
            "--home",
            str(home),
            "--idle-seconds",
            "1",
            "--alert-cooldown",
            "0",
            "--from-start",
            "--once",
        ]
    )

    assert watchdog.run(args) == 0

    alerts = home / "logs" / "live-watchdog-alerts.jsonl"
    rows = [json.loads(line) for line in alerts.read_text(encoding="utf-8").splitlines()]
    events = [row["event"] for row in rows]
    assert "turn_stuck" not in events
    assert "personal_assistant_turn_stuck" not in events


def test_all_interactive_request_events_enter_waiting_state():
    for event in (
        "clarify.request",
        "approval.request",
        "terminal.read.request",
        "sudo.request",
        "secret.request",
        "input.request",
    ):
        state = watchdog.TurnState(session_id="sid")
        state.update({"event": event, "monotonic": 10})
        assert state.waiting is True, event


def test_explicit_resume_leaves_waiting_and_restarts_progress_clock():
    state = watchdog.TurnState(session_id="sid")
    state.update({"event": "clarify.request", "monotonic": 10})

    state.update({"event": "clarify.resume", "monotonic": 20})

    assert state.waiting is False
    assert state.last_progress_at == 20
    assert state.last_event == "clarify.resume"


def test_turn_state_key_separates_ledgers_sessions_and_turns(tmp_path):
    ledger_a = tmp_path / "a.jsonl"
    ledger_b = tmp_path / "b.jsonl"
    base = {"session_id": "sid", "turn_id": "turn-1"}

    assert watchdog.turn_state_key(ledger_a, base) != watchdog.turn_state_key(
        ledger_b, base
    )
    assert watchdog.turn_state_key(ledger_a, base) != watchdog.turn_state_key(
        ledger_a, {**base, "turn_id": "turn-2"}
    )


def test_dead_producer_emits_one_orphan_incident_instead_of_stuck(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    monkeypatch.setattr(watchdog, "producer_identity_alive", lambda _pid, _ticks: False)
    home = tmp_path / ".hermes"
    ledger = home / "logs" / "turn-watchdog.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps(
            {
                "monotonic": time.time() - 90,
                "event": "message.start",
                "session_id": "sid-orphan",
                "session_key": "key-orphan",
                "turn_id": "turn-orphan",
                "producer_pid": 123,
                "producer_start_ticks": 456,
                "running": True,
                "turn_started_at": time.time() - 120,
                "payload": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    args = watchdog.parse_args(
        ["--home", str(home), "--idle-seconds", "1", "--from-start", "--once"]
    )
    assert watchdog.run(args) == 0

    rows = [
        json.loads(line)
        for line in (home / "logs" / "live-watchdog-alerts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [row["event"] for row in rows].count("turn_orphaned") == 1
    assert "turn_stuck" not in {row["event"] for row in rows}


def test_pid_reuse_is_not_treated_as_the_original_producer(monkeypatch):
    monkeypatch.setattr(watchdog, "process_start_ticks", lambda _pid: 999)

    assert watchdog.producer_identity_alive(123, 456) is False


def test_flowstate_signed_out_requires_auth_and_uses_extended_backoff():
    decision = watchdog.classify_flowstate_recovery(
        status="signed_out",
        health_ok=False,
        config={"enabled": True, "port": 5577},
        app_running=False,
    )
    args = watchdog.parse_args(["--alert-cooldown", "1"])
    incident = {
        "event": "personal_assistant_monitor_connector_failure",
        "status": "signed_out",
    }

    assert decision == {
        "action": "none",
        "outcome": "auth_required",
        "reason": "flowstate_sign_in_required",
    }
    assert watchdog.incident_cooldown_seconds(args, incident) >= 20 * 60


def test_personal_assistant_stuck_turn_is_classified(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    ledger = home / "profiles" / "office-work" / "logs" / "turn-watchdog.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps(
            {
                "monotonic": time.time() - 90,
                "event": "message.start",
                "session_id": "sid-assistant",
                "session_key": "assistant-key",
                "running": True,
                "personal_assistant": True,
                "turn_started_at": time.time() - 120,
                "payload": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    args = watchdog.parse_args(
        [
            "--home",
            str(home),
            "--idle-seconds",
            "45",
            "--alert-cooldown",
            "0",
            "--from-start",
            "--once",
        ]
    )

    assert watchdog.run(args) == 0

    alerts = home / "logs" / "live-watchdog-alerts.jsonl"
    rows = [json.loads(line) for line in alerts.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["event"] == "personal_assistant_turn_stuck"
    assert rows[-1]["personal_assistant"] is True


def test_post_completion_review_summary_does_not_rearm_stuck_turn(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    ledger = home / "profiles" / "office-work" / "logs" / "turn-watchdog.jsonl"
    ledger.parent.mkdir(parents=True)
    base = {
        "monotonic": time.time() - 90,
        "session_id": "sid-complete",
        "session_key": "assistant-key",
        "personal_assistant": True,
    }
    ledger.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in (
                {**base, "event": "message.complete", "running": True, "terminal_emitted": True},
                {**base, "event": "review.summary", "running": False, "terminal_emitted": True},
            )
        ),
        encoding="utf-8",
    )

    args = watchdog.parse_args(
        ["--home", str(home), "--idle-seconds", "1", "--from-start", "--once"]
    )

    assert watchdog.run(args) == 0

    alerts = home / "logs" / "live-watchdog-alerts.jsonl"
    events = [json.loads(line)["event"] for line in alerts.read_text(encoding="utf-8").splitlines()]
    assert "personal_assistant_turn_stuck" not in events
    assert "turn_stuck" not in events


def test_legacy_terminal_row_without_turn_timestamp_clears_session_state(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    ledger = home / "logs" / "turn-watchdog.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in (
                {
                    "event": "message.start",
                    "monotonic": time.time() - 90,
                    "session_id": "legacy-sid",
                    "running": True,
                    "turn_started_at": time.time() - 120,
                },
                {
                    "event": "session.info",
                    "monotonic": time.time() - 89,
                    "session_id": "legacy-sid",
                    "running": False,
                },
            )
        ),
        encoding="utf-8",
    )

    args = watchdog.parse_args(
        ["--home", str(home), "--idle-seconds", "1", "--from-start", "--once"]
    )
    assert watchdog.run(args) == 0

    events = {
        json.loads(line)["event"]
        for line in (home / "logs" / "live-watchdog-alerts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    }
    assert "turn_stuck" not in events


def test_session_not_found_is_alerted_immediately(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    ledger = home / "logs" / "turn-watchdog.jsonl"
    monkeypatch.setattr(server, "_TURN_WATCHDOG_LOG", str(ledger))
    server._sessions.pop("sid-stale", None)

    response = server.handle_request(
        {
            "id": "rpc-1",
            "method": "prompt.submit",
            "params": {
                "session_id": "sid-stale",
                "text": "private prompt content",
            },
        }
    )
    assert response["error"]["message"] == "session not found"
    assert "private prompt content" not in ledger.read_text(encoding="utf-8")
    rpc_row = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert watchdog.is_terminal(rpc_row) is True

    args = watchdog.parse_args(
        ["--home", str(home), "--from-start", "--once"]
    )

    assert watchdog.run(args) == 0

    alerts = home / "logs" / "live-watchdog-alerts.jsonl"
    rows = [json.loads(line) for line in alerts.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["event"] == "session_not_found"
    assert rows[-1]["session_id"] == "sid-stale"
    assert rows[-1]["payload"] == {
        "error": "session not found",
        "method": "prompt.submit",
    }


def test_session_not_found_diagnostic_and_error_are_deduplicated(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    ledger = home / "logs" / "turn-watchdog.jsonl"
    ledger.parent.mkdir(parents=True)
    base = {
        "monotonic": time.time(),
        "session_id": "sid-stale",
        "session_key": "key-stale",
        "running": True,
    }
    rows = [
        {
            **base,
            "event": "diagnostic.event",
            "payload": {
                "component": "turn",
                "event": "error",
                "details": {"error": "session not found"},
            },
        },
        {
            **base,
            "event": "error",
            "payload": {"message": "session not found"},
        },
    ]
    ledger.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    args = watchdog.parse_args(["--home", str(home), "--from-start", "--once"])
    assert watchdog.run(args) == 0

    alerts = home / "logs" / "live-watchdog-alerts.jsonl"
    emitted = [json.loads(line) for line in alerts.read_text(encoding="utf-8").splitlines()]
    assert [row["event"] for row in emitted].count("session_not_found") == 1


def test_missing_session_during_idempotent_cleanup_does_not_alert(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    ledger = home / "logs" / "turn-watchdog.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps(
            {
                "monotonic": time.time(),
                "event": "rpc.error",
                "session_id": "ghost",
                "session_key": "",
                "running": False,
                "payload": {
                    "error": "session not found",
                    "method": "session.delete",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    args = watchdog.parse_args(["--home", str(home), "--from-start", "--once"])
    assert watchdog.run(args) == 0

    alerts = home / "logs" / "live-watchdog-alerts.jsonl"
    emitted = [json.loads(line) for line in alerts.read_text(encoding="utf-8").splitlines()]
    assert "session_not_found" not in {row["event"] for row in emitted}


def test_missing_session_during_read_only_status_poll_does_not_alert(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    ledger = home / "logs" / "turn-watchdog.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        "".join(
            json.dumps(
                {
                    "monotonic": time.time(),
                    "event": "rpc.error",
                    "session_id": "retired-runtime",
                    "session_key": "",
                    "running": False,
                    "payload": {
                        "error": "session not found",
                        "method": method,
                    },
                }
            )
            + "\n"
            for method in ("session.usage", "process.list")
        ),
        encoding="utf-8",
    )

    args = watchdog.parse_args(["--home", str(home), "--from-start", "--once"])
    assert watchdog.run(args) == 0

    alerts = home / "logs" / "live-watchdog-alerts.jsonl"
    emitted = [json.loads(line) for line in alerts.read_text(encoding="utf-8").splitlines()]
    assert "session_not_found" not in {row["event"] for row in emitted}
