import importlib.util
import json
import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tui_gateway import server


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hermes_live_watchdog.py"
SPEC = importlib.util.spec_from_file_location("hermes_live_watchdog", SCRIPT)
watchdog = importlib.util.module_from_spec(SPEC)
assert SPEC is not None
assert SPEC.loader is not None
sys.modules[SPEC.name] = watchdog
SPEC.loader.exec_module(watchdog)


def _load_improvement_supervisor():
    package_name = "hermes_plugins.improvement_supervisor"
    plugin_dir = SCRIPT.parents[1] / "plugins" / "improvement-supervisor"
    for name in list(sys.modules):
        if name == package_name or name.startswith(f"{package_name}."):
            sys.modules.pop(name, None)
    if "hermes_plugins" not in sys.modules:
        namespace = types.ModuleType("hermes_plugins")
        namespace.__path__ = []
        sys.modules["hermes_plugins"] = namespace
    spec = importlib.util.spec_from_file_location(
        package_name,
        plugin_dir / "__init__.py",
        submodule_search_locations=[str(plugin_dir)],
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    module.__package__ = package_name
    module.__path__ = [str(plugin_dir)]
    sys.modules[package_name] = module
    spec.loader.exec_module(module)
    module._set_runtime_snapshot_for_tests(
        lambda root, _digest, fingerprint: root
        / ".test-repair-snapshots"
        / fingerprint[:12]
    )
    return module


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


def test_failed_tool_call_is_alerted_immediately_for_repair(tmp_path):
    alert = watchdog.build_incident_alert(
        {
            "session_id": "runtime-private",
            "event": "tool.complete",
            "payload": {
                "name": "personal_assistant_interview_start",
                "duration_s": 0.004,
                "status": "error",
                "error": "unknown",
            },
        },
        tmp_path / "turn-watchdog.jsonl",
    )

    assert alert["event"] == "tool_failure"
    assert alert["message"] == "Hermes tool call failure detected"
    assert "sent for repair" not in alert["message"].lower()
    assert alert["tool"] == "personal_assistant_interview_start"
    assert alert["error_category"] == "unknown"
    assert alert["incident"]["taxonomy"] == "tool_failure"
    assert "runtime-private" not in json.dumps(alert)


@pytest.mark.parametrize(
    ("message", "category"),
    [
        ("Permission denied while searching a child directory", "filesystem_permission"),
        ("BLOCKED: execute_code requires approval", "policy_blocked"),
        (
            "Background delivery is not available for an occluded, unfocused renderer",
            "ui_unavailable",
        ),
    ],
)
def test_watchdog_preserves_actionable_tool_failure_categories(message, category):
    assert watchdog._error_category(message) == category


def test_failed_tool_call_run_feeds_the_improvement_supervisor(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    ledger = home / "profiles" / "office-work" / "logs" / "turn-watchdog.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps(
            {
                "event": "tool.complete",
                "monotonic": time.time(),
                "payload": {
                    "name": "execute_code",
                    "duration_s": 0.074,
                    "status": "error",
                    "error": "validation",
                },
                "session_id": "runtime-private",
                "turn_id": "turn-private",
                "cwd": str(tmp_path),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    args = watchdog.parse_args(["--home", str(home), "--from-start", "--once"])
    assert watchdog.run(args) == 0

    inbox = (
        home
        / "profiles"
        / "office-work"
        / "state"
        / "improvement-supervisor"
        / "runtime-events.jsonl"
    )
    event = json.loads(inbox.read_text(encoding="utf-8"))
    assert event["failure"]["taxonomy"] == "tool_failure"
    assert event["failure"]["code"] == "tool_failure"
    assert event["tool"]["name"] == "execute_code"
    assert event["tool"]["status"] == "error"


def test_watchdog_run_writes_a_fresh_user_visible_heartbeat(tmp_path):
    home = tmp_path / ".hermes"

    args = watchdog.parse_args(["--home", str(home), "--once"])
    assert watchdog.run(args) == 0

    status = json.loads(
        (home / "logs" / "live-watchdog-status.json").read_text(encoding="utf-8")
    )
    assert status["state"] == "running"
    assert status["heartbeatAt"]
    assert status["startedAt"]
    assert status["watchedSources"] >= 3
    assert "pid" not in status


def test_watchdog_run_ticks_repair_worker_after_ingestion(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        watchdog,
        "_ingest_improvement_supervisor_events",
        lambda _home: calls.append("ingest") or True,
    )
    monkeypatch.setattr(
        watchdog,
        "_tick_improvement_supervisor_repair",
        lambda _home: calls.append("repair") or "idle",
        raising=False,
    )

    args = watchdog.parse_args(["--home", str(tmp_path / ".hermes"), "--once"])
    assert watchdog.run(args) == 0
    assert calls == ["ingest", "repair"]


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
    assert alert["incident"]["taxonomy"] == "compression_stall"
    assert alert["incident"]["phase"] == "compression"
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


def test_flowstate_process_probe_ignores_defunct_and_child_processes(monkeypatch):
    monkeypatch.setattr(
        watchdog.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                "Z    [flowstate] <defunct>\n"
                "S    /tmp/.mount_FlowSt123/flowstate --type=zygote --no-sandbox\n"
                "S    /tmp/.mount_FlowSt123/flowstate --type=renderer --no-sandbox\n"
            ),
        ),
    )

    assert watchdog.flowstate_app_running() is False


def test_flowstate_process_probe_accepts_only_a_live_primary_process(monkeypatch):
    monkeypatch.setattr(
        watchdog.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="Sl   /tmp/.mount_FlowSt123/flowstate --no-sandbox --ozone-platform=x11\n",
        ),
    )

    assert watchdog.flowstate_app_running() is True


def test_flowstate_launch_uses_desktop_wrapper_and_current_xauthority(tmp_path, monkeypatch):
    launcher = tmp_path / ".local" / "bin" / "FlowState-launch.sh"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    xauthority = runtime_dir / "xauth_current"
    xauthority.write_text("cookie", encoding="utf-8")
    launched = []
    monkeypatch.setattr(watchdog.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.delenv("XAUTHORITY", raising=False)
    monkeypatch.setattr(
        watchdog.subprocess,
        "Popen",
        lambda command, **kwargs: launched.append((command, kwargs)) or SimpleNamespace(pid=1),
    )

    assert watchdog.launch_flowstate_app() is True
    command, kwargs = launched[0]
    assert command == [str(launcher)]
    assert kwargs["env"]["DISPLAY"] == ":0"
    assert kwargs["env"]["XAUTHORITY"] == str(xauthority)
    assert kwargs["start_new_session"] is True


def test_flowstate_recovery_restarts_running_but_unhealthy_app():
    decision = watchdog.classify_flowstate_recovery(
        status="unavailable",
        health_ok=False,
        config={"enabled": True, "port": 5577, "token": "secret"},
        app_running=True,
    )

    assert decision["action"] == "restart"
    assert decision["outcome"] == "repair_started"


def test_running_but_unhealthy_flowstate_is_restarted_and_verified(
    tmp_path, monkeypatch
):
    home = tmp_path / ".hermes"
    config_path = tmp_path / "local-api.json"
    config_path.write_text(
        json.dumps({"enabled": True, "port": 5577, "token": "do-not-persist"}),
        encoding="utf-8",
    )
    health = iter([False, False, True])
    restarted = []
    monkeypatch.setattr(watchdog, "flowstate_config_path", lambda: config_path)
    monkeypatch.setattr(watchdog, "flowstate_health_ok", lambda _port: next(health))
    monkeypatch.setattr(watchdog, "flowstate_app_running", lambda: True)
    monkeypatch.setattr(
        watchdog,
        "restart_flowstate_app",
        lambda: restarted.append(True) or True,
    )
    monkeypatch.setattr(watchdog.time, "sleep", lambda _seconds: None)

    result = watchdog.attempt_flowstate_recovery(
        home=home,
        profile="office-work",
        status="unavailable",
        verify_attempts=3,
    )

    assert restarted == [True]
    assert result["outcome"] == "repaired"


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


def test_tool_error_followed_by_stall_has_rich_privacy_safe_context(tmp_path):
    state = watchdog.TurnState(session_id="private-runtime-id")
    state.update(
        {
            "event": "tool.start",
            "monotonic": 10,
            "payload": {
                "name": "web_search",
                "args": {"query": "private medical details"},
            },
        }
    )
    state.update(
        {
            "event": "tool.error",
            "monotonic": 12,
            "payload": {
                "name": "web_search",
                "error": "ReadTimeout while searching private medical details",
                "details": {
                    "error_type": "ReadTimeout",
                    "status": 504,
                    "retry_count": 1,
                    "max_retries": 2,
                    "cancel_requested": True,
                    "queue_pending": True,
                    "queued_turn_durable": True,
                    "build_commit": "abcdef1234567890",
                    "runtime": "desktop",
                },
            },
        }
    )

    alert = watchdog.build_alert(state, 60, 75, 45, tmp_path / "turn-watchdog.jsonl")
    context = alert["incident"]

    assert context["taxonomy"] == "tool_error_followed_by_stall"
    assert context["severity"] == "error"
    assert context["failure_level"] == "degraded"
    assert context["phase"] == "tool"
    assert context["last_real_progress"]["event"] == "tool.error"
    assert context["tool"] == {"name": "web_search"}
    assert context["error"] == {
        "category": "timeout",
        "code": "504",
        "type": "ReadTimeout",
    }
    assert context["retry"] == {"attempt": 1, "max_attempts": 2}
    assert context["cancel"] == {"requested": True}
    assert context["queue"] == {"durable": True, "pending": True}
    assert context["build"]["commit"] == "abcdef123456"
    assert context["runtime"]["surface"] == "desktop"
    serialized = json.dumps(context)
    assert "private" not in serialized
    assert "query" not in serialized
    assert "session" not in serialized


def test_incident_signature_is_stable_across_private_ids_and_content(tmp_path):
    def alert_for(session_id: str, prompt: str):
        state = watchdog.TurnState(session_id=session_id)
        state.update(
            {
                "event": "tool.error",
                "monotonic": 12,
                "payload": {
                    "name": "flowstate_search",
                    "error": f"connection reset while handling {prompt}",
                    "details": {"error_type": "ConnectionResetError"},
                },
            }
        )
        return watchdog.build_alert(
            state, 60, 75, 45, tmp_path / "turn-watchdog.jsonl"
        )["incident"]

    first = alert_for("secret-session-one", "my private task")
    second = alert_for("secret-session-two", "different private task")

    assert first["signature"] == second["signature"]
    assert first["error"]["category"] == "connection"


def test_stall_context_keeps_tool_error_as_last_real_progress_after_heartbeat(tmp_path):
    state = watchdog.TurnState(session_id="private-runtime-id")
    state.update(
        {
            "event": "tool.error",
            "monotonic": 12,
            "payload": {
                "name": "flowstate_search",
                "error": "connection reset while handling private content",
                "details": {"error_type": "ConnectionResetError"},
            },
        }
    )
    state.update(
        {
            "event": "diagnostic.event",
            "monotonic": 13,
            "payload": {"component": "turn", "event": "heartbeat"},
        }
    )

    context = watchdog.build_alert(
        state, 60, 75, 45, tmp_path / "turn-watchdog.jsonl"
    )["incident"]

    assert context["taxonomy"] == "tool_error_followed_by_stall"
    assert context["last_real_progress"]["event"] == "tool.error"
    assert context["tool"] == {"name": "flowstate_search"}
    assert context["error"]["category"] == "connection"


def test_failed_tool_complete_is_classified_as_tool_error_followed_by_stall(tmp_path):
    state = watchdog.TurnState(session_id="private-runtime-id")
    state.update(
        {
            "event": "tool.complete",
            "monotonic": 12,
            "payload": {
                "name": "flowstate_search",
                "duration_ms": 905,
                "status": "error",
                "error_category": "connection",
            },
        }
    )

    context = watchdog.build_alert(
        state, 60, 75, 45, tmp_path / "turn-watchdog.jsonl"
    )["incident"]

    assert context["taxonomy"] == "tool_error_followed_by_stall"
    assert context["tool"] == {"name": "flowstate_search"}
    assert context["error"] == {"category": "connection"}


def test_watchdog_reconciles_a_successful_retry_after_tool_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    ledger = home / "logs" / "turn-watchdog.jsonl"
    ledger.parent.mkdir(parents=True)
    rows = [
        {
            "event": "tool.complete",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "payload": {
                "name": "personal_assistant_safety_review",
                "status": "error",
                "error_category": "validation",
            },
        },
        {
            "event": "tool.complete",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "payload": {
                "name": "personal_assistant_safety_review",
                "status": "complete",
            },
        },
    ]
    ledger.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    args = watchdog.parse_args(
        [
            "--home",
            str(home),
            "--from-start",
            "--once",
            "--alert-cooldown",
            "0",
        ]
    )

    assert watchdog.run(args) == 0
    alerts = [
        json.loads(line)
        for line in (home / "logs" / "live-watchdog-alerts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    recovered = next(item for item in alerts if item["event"] == "tool_recovered")

    assert recovered["tool"] == "personal_assistant_safety_review"
    assert recovered["recovery_outcome"] == "verified_retry_succeeded"
    assert recovered["severity"] == "info"


def test_process_snapshot_never_copies_process_arguments_and_is_bounded(monkeypatch):
    private_argument = "private-token-never-copy"
    stdout = "\n".join(
        f"{100 + index} /private/path/Hermes-{index} --token {private_argument}"
        for index in range(20)
    )
    monkeypatch.setattr(
        watchdog.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=stdout),
    )

    snapshot = watchdog.process_snapshot()

    assert len(snapshot) == 12
    assert snapshot[0] == {"pid": 100, "executable": "Hermes-0"}
    assert private_argument not in json.dumps(snapshot)
    assert "/private/path" not in json.dumps(snapshot)


def test_alert_log_rotation_is_bounded_to_two_backups(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "MAX_ALERT_LOG_BYTES", 180)
    path = tmp_path / "live-watchdog-alerts.jsonl"

    for index in range(12):
        watchdog.append_alert_jsonl(path, {"event": f"failure-{index}", "pad": "x" * 55})

    assert path.exists()
    assert path.with_name(path.name + ".1").exists()
    assert path.with_name(path.name + ".2").exists()
    assert not path.with_name(path.name + ".3").exists()
    assert all(
        candidate.stat().st_size <= 180
        for candidate in (
            path,
            path.with_name(path.name + ".1"),
            path.with_name(path.name + ".2"),
        )
    )


def test_alert_log_discards_an_oversized_legacy_segment_on_rotation(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(watchdog, "MAX_ALERT_LOG_BYTES", 180)
    path = tmp_path / "live-watchdog-alerts.jsonl"
    path.write_text("legacy-private-content" * 100, encoding="utf-8")

    watchdog.append_alert_jsonl(path, {"event": "new-safe-alert"})

    assert json.loads(path.read_text(encoding="utf-8"))["event"] == "new-safe-alert"
    assert not path.with_name(path.name + ".1").exists()


def test_single_oversized_alert_is_reduced_to_safe_incident_envelope(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(watchdog, "MAX_ALERT_LOG_BYTES", 220)
    path = tmp_path / "live-watchdog-alerts.jsonl"

    watchdog.append_alert_jsonl(
        path,
        {
            "ts": "now",
            "severity": "error",
            "component": "live_watchdog",
            "event": "turn_stuck",
            "payload": {"prompt": "private" * 1000},
        },
    )

    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["event"] == "turn_stuck"
    assert row["evidence_truncated"] is True
    assert "private" not in json.dumps(row)
    assert path.stat().st_size <= 220


def test_incident_taxonomy_covers_runtime_failure_phases():
    cases = [
        ({"event": "provider.timeout", "payload": {}}, "provider_stall", "provider"),
        (
            {
                "event": "diagnostic.event",
                "payload": {"component": "compression", "event": "timeout"},
            },
            "compression_stall",
            "compression",
        ),
        ({"event": "clarify.request", "payload": {}}, "user_wait", "user_wait"),
        ({"event": "queue.error", "payload": {}}, "queue_failure", "queue"),
        ({"event": "gateway.reconnect", "payload": {}}, "reconnect_failure", "reconnect"),
        ({"event": "turn.orphaned", "payload": {}}, "orphaned_turn", "orphan"),
    ]

    for row, taxonomy, phase in cases:
        context = watchdog.build_incident_context(row=row)
        assert context["taxonomy"] == taxonomy
        assert context["phase"] == phase
        assert len(context["signature"]) == 24


def test_composer_action_mismatch_becomes_queue_incident(tmp_path):
    row = {
        "component": "composer",
        "event": "queue.action_mismatch",
        "severity": "error",
        "message": "Composer control performed a different action than it presented",
        "details": {"presented_action": "queue", "actual_action": "answer"},
    }

    alert = watchdog.build_incident_alert(row, tmp_path / "desktop-events.jsonl")

    assert alert is not None
    assert alert["event"] == "composer_action_mismatch"
    assert alert["presented_action"] == "queue"
    assert alert["actual_action"] == "answer"
    assert alert["incident"]["taxonomy"] == "queue_failure"


def test_incident_context_keeps_fixed_runtime_source_identity():
    context = watchdog.build_incident_context(
        row={
            "event": "tool.error",
            "runtime_build_id": "source-abc123-def456",
            "source_manifest_digest": "a" * 64,
            "payload": {"name": "patch", "status": "error"},
        }
    )

    assert context["build"] == {
        "id": "source-abc123-def456",
        "source_manifest_digest": "a" * 64,
    }


def test_incident_context_is_bounded_and_only_keeps_allowlisted_metadata():
    context = watchdog.build_incident_context(
        row={
            "event": "tool.error",
            "session_id": "never-copy-me",
            "payload": {
                "name": "x" * 500,
                "error": "secret=" + "y" * 5000,
                "text": "private prompt",
                "details": {
                    "error_type": "CustomProviderFailure" * 20,
                    "error_code": "E_CONNRESET",
                    "queue_count": 999999,
                    "resume_pending": True,
                    "persisted": True,
                    "build_version": "1.2.3-private-extra",
                    "unknown": "must not survive",
                },
            },
        }
    )

    assert len(context["tool"]["name"]) <= 80
    assert len(context["error"]["type"]) <= 80
    assert context["queue"]["count"] == 1000
    assert context["persistence"] == {"persisted": True, "resume_pending": True}
    serialized = json.dumps(context)
    assert "never-copy-me" not in serialized
    assert "private prompt" not in serialized
    assert "must not survive" not in serialized
    assert len(serialized) < 3000


def test_stuck_turn_run_feeds_plugin_normalizable_watchdog_incident(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    profile = "planning"
    ledger = home / "profiles" / profile / "logs" / "turn-watchdog.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps(
            {
                "monotonic": time.time() - 90,
                "event": "tool.complete",
                "session_id": "private-session",
                "session_key": "private-key",
                "turn_id": "private-turn",
                "cwd": str(SCRIPT.parents[1]),
                "runtime_build_id": "source-abc123-def456",
                "source_manifest_digest": "a" * 64,
                "running": True,
                "turn_started_at": time.time() - 120,
                "payload": {
                    "name": "flowstate_search",
                    "status": "error",
                    "error_category": "connection",
                },
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
            "1",
            "--alert-cooldown",
            "0",
            "--from-start",
            "--once",
        ]
    )
    assert watchdog.run(args) == 0
    assert watchdog.run(args) == 0

    inbox = (
        home
        / "profiles"
        / profile
        / "state"
        / "improvement-supervisor"
        / "runtime-events.jsonl"
    )
    events = [
        json.loads(line)
        for line in inbox.read_text(encoding="utf-8").splitlines()
    ]
    event = next(
        item
        for item in events
        if item["failure"]["taxonomy"] == "tool_failure"
    )
    plugin = _load_improvement_supervisor()
    bundle = plugin._normalize_watchdog_incident(event)

    assert bundle is not None
    assert event["event"] == "watchdog_incident"
    assert event["schema_version"] == 1
    assert event["profile"] == profile
    assert bundle["failure"]["taxonomy"] == "tool_failure"
    assert bundle["tool"]["name"] == "flowstate_search"
    assert bundle["source"]["runtime_build_id"] == "source-abc123-def456"
    assert bundle["source"]["source_manifest_digest"] == "a" * 64
    assert len(events) == 4
    assert len({item["event_id"] for item in events}) == 2
    assert len(json.dumps(event)) < 8192
    assert "private-session" not in json.dumps(event)
    assert "private-key" not in json.dumps(event)


def test_watchdog_run_consumes_warning_incident_without_plugin_backend(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    profile = "office-work"
    inbox = (
        home
        / "profiles"
        / profile
        / "state"
        / "improvement-supervisor"
        / "runtime-events.jsonl"
    )
    inbox.parent.mkdir(parents=True)
    inbox.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "event_id": "always-on-warning",
                "event": "watchdog_incident",
                "observed_at": "2026-07-20T18:00:00Z",
                "severity": "warning",
                "failure": {
                    "taxonomy": "renderer.artifact_warning",
                    "component": "desktop_renderer",
                    "code": "form_rejected",
                },
                "source": {"repo_root": str(SCRIPT.parents[1]), "revision": "abc"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(watchdog, "_improvement_supervisor_ingester", None)

    args = watchdog.parse_args(["--home", str(home), "--once"])
    assert watchdog.run(args) == 0

    proposal_path = inbox.parent / "proposals.json"
    proposals = json.loads(proposal_path.read_text(encoding="utf-8"))["proposals"]
    assert len(proposals) == 1
    assert proposals[0]["dedup_key"].startswith("watchdog-")
    assert json.loads((inbox.parent / "runtime-events-seen.json").read_text()) == [
        "always-on-warning"
    ]


def test_watchdog_run_feeds_error_incident_to_kanban_once(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    profile = "office-work"
    inbox = (
        home
        / "profiles"
        / profile
        / "state"
        / "improvement-supervisor"
        / "runtime-events.jsonl"
    )
    inbox.parent.mkdir(parents=True)
    event = {
        "schema_version": 1,
        "event_id": "always-on-error",
        "event": "watchdog_incident",
        "observed_at": "2026-07-20T18:00:00Z",
        "severity": "error",
        "failure": {
            "taxonomy": "queue.acceptance",
            "component": "desktop_composer",
            "code": "queue_push_rejected",
        },
        "source": {
            "repo_root": str(SCRIPT.parents[1]),
            "revision": "abc",
            "runtime_build_id": "test-build",
            "source_manifest_digest": "a" * 64,
        },
    }
    inbox.write_text(json.dumps(event) + "\n", encoding="utf-8")
    plugin = _load_improvement_supervisor()

    class _Connection:
        def close(self):
            return None

    class _Kanban:
        def __init__(self):
            self.tasks = {}
            self.created = []
            self.attachments = []
            self.board_metadata = {}

        def connect(self, *, board=None):
            return _Connection()

        def read_board_metadata(self, board):
            return {"slug": board, **self.board_metadata}

        def write_board_metadata(self, board, **kwargs):
            self.board_metadata.update(kwargs)
            return {"slug": board, **self.board_metadata}

        def get_task(self, _conn, task_id):
            return self.tasks.get(task_id)

        def create_task(self, _conn, **kwargs):
            self.created.append(kwargs)
            self.tasks["repair-1"] = SimpleNamespace(status="ready")
            return "repair-1"

        def store_attachment_bytes(self, _conn, task_id, filename, data, **kwargs):
            self.attachments.append(task_id)
            return 1

        def add_comment(self, *_args, **_kwargs):
            return 1

    kanban = _Kanban()
    plugin._set_kanban_for_tests(kanban)
    monkeypatch.setattr(
        watchdog,
        "_load_improvement_supervisor_ingester",
        lambda: plugin.ingest_runtime_events_for_root,
    )
    monkeypatch.setattr(watchdog, "_improvement_supervisor_ingester", None)

    args = watchdog.parse_args(["--home", str(home), "--once"])
    assert watchdog.run(args) == 0
    assert watchdog.run(args) == 0

    assert len(kanban.created) == 1
    assert kanban.attachments == ["repair-1"]


def test_orphan_run_feeds_watchdog_incident_to_ledger_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    monkeypatch.setattr(watchdog, "producer_identity_alive", lambda _pid, _ticks: False)
    home = tmp_path / ".hermes"
    profile = "film-maker"
    ledger = home / "profiles" / profile / "logs" / "turn-watchdog.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps(
            {
                "monotonic": time.time() - 90,
                "event": "message.start",
                "session_id": "private-session",
                "turn_id": "private-turn",
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

    inbox = home / "profiles" / profile / "state" / "improvement-supervisor" / "runtime-events.jsonl"
    event = json.loads(inbox.read_text(encoding="utf-8"))
    assert event["event"] == "watchdog_incident"
    assert event["failure"]["taxonomy"] == "orphaned_turn"
    assert event["backend"]["pid"] == 123


def test_terminal_incident_run_feeds_watchdog_incident_to_profile(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    profile = "office-work"
    ledger = home / "profiles" / profile / "logs" / "turn-watchdog.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps(
            {
                "monotonic": time.time(),
                "event": "rpc.error",
                "session_id": "private-session",
                "running": False,
                "payload": {
                    "error": "session not found",
                    "method": "prompt.submit",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    args = watchdog.parse_args(["--home", str(home), "--from-start", "--once"])
    assert watchdog.run(args) == 0

    inbox = home / "profiles" / profile / "state" / "improvement-supervisor" / "runtime-events.jsonl"
    event = json.loads(inbox.read_text(encoding="utf-8"))
    assert event["event"] == "watchdog_incident"
    assert event["failure"]["code"] == "session_not_found"
    assert event["failure"]["taxonomy"] == "runtime_failure"
    assert Path(event["source"]["repo_root"]) == SCRIPT.parents[1]
    assert (Path(event["source"]["repo_root"]) / ".git").exists()


def test_tool_iteration_exhaustion_is_classified_and_fed_to_supervisor(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    profile = "office-work"
    ledger = home / "profiles" / profile / "logs" / "turn-watchdog.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps(
            {
                "monotonic": time.time(),
                "event": "diagnostic.event",
                "session_id": "private-session",
                "running": True,
                "cwd": str(SCRIPT.parents[1]),
                "payload": {
                    "component": "turn",
                    "event": "tool_iteration_exhausted",
                    "message": "Hermes exhausted the tool iteration budget",
                    "details": {"iteration_count": 60, "iteration_limit": 60},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    args = watchdog.parse_args(["--home", str(home), "--from-start", "--once"])
    assert watchdog.run(args) == 0

    inbox = home / "profiles" / profile / "state" / "improvement-supervisor" / "runtime-events.jsonl"
    event = json.loads(inbox.read_text(encoding="utf-8"))
    assert event["event"] == "watchdog_incident"
    assert event["severity"] == "error"
    assert event["failure"]["taxonomy"] == "tool_iteration_exhausted"
    assert event["failure"]["code"] == "tool_iteration_exhausted"
    assert event["tool"]["attempt"] == 60
    assert "private-session" not in json.dumps(event)
