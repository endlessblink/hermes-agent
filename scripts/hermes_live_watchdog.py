#!/usr/bin/env python3
"""Out-of-band live watchdog for Hermes Desktop turns.

The gateway writes a lightweight turn ledger to
``~/.hermes/logs/turn-watchdog.jsonl`` or a profile-local ledger under
``~/.hermes/profiles/<profile>/logs/turn-watchdog.jsonl``. This process tails
those ledgers outside the agent turn and alerts when a session is running
without visible progress. It is intentionally independent from Desktop UI state:
if the UI or turn thread is wedged, this still leaves a forensic alert trail.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_IDLE_SECONDS = 45.0
DEFAULT_ALERT_COOLDOWN_SECONDS = 60.0
DEFAULT_MONITOR_PRODUCER_STALE_SECONDS = 20 * 60.0
DEFAULT_MONITOR_CONSUMER_STALE_SECONDS = 150.0
LEDGER_REFRESH_SECONDS = 10.0


def hermes_home() -> Path:
    raw = os.environ.get("HERMES_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".hermes"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def load_json(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def process_snapshot() -> list[str]:
    try:
        result = subprocess.run(
            ["pgrep", "-af", "Hermes|hermes.*serve|slash_worker"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
    except Exception:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def desktop_notify(title: str, body: str) -> None:
    notify = shutil.which("notify-send")
    if not notify:
        return
    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    try:
        subprocess.run(
            [notify, "--app-name=Hermes Watchdog", "--urgency=critical", title, body],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except Exception:
        return


@dataclass
class TurnState:
    session_id: str
    session_key: str = ""
    cwd: str = ""
    started_at: float = 0.0
    last_progress_at: float = 0.0
    last_event: str = ""
    compression: bool = False
    personal_assistant: bool = False
    payload: dict[str, Any] = field(default_factory=dict)
    last_alert_at: float = 0.0

    def update(self, row: dict[str, Any]) -> None:
        # Ledger timestamps are Unix time despite the historical ``monotonic``
        # field name. Keep the watchdog in that same clock domain so persisted
        # rows can be compared after process restarts.
        now = float(row.get("monotonic") or time.time())
        self.session_key = str(row.get("session_key") or self.session_key)
        self.cwd = str(row.get("cwd") or self.cwd)
        self.last_event = str(row.get("event") or self.last_event)
        self.last_progress_at = now
        self.payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if not self.started_at:
            self.started_at = float(row.get("turn_started_at") or now)
        self.compression = bool(row.get("compression_started_at")) or (
            self.payload.get("component") == "compression"
            and self.payload.get("event") in {"start", "heartbeat"}
        )
        self.personal_assistant = bool(
            row.get("personal_assistant", self.personal_assistant)
        )


def is_terminal(row: dict[str, Any]) -> bool:
    event = str(row.get("event") or "")
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    if event in {"message.complete", "rpc.error"}:
        return True
    if event == "diagnostic.event" and payload.get("component") == "turn":
        return payload.get("event") in {"complete", "error", "finally", "idle_timeout"}
    if event == "diagnostic.event" and payload.get("component") == "compression":
        return payload.get("event") == "timeout"
    return False


def is_progress(row: dict[str, Any]) -> bool:
    # Background review summaries and other trailing events can land after the
    # turn has already completed. They are useful history, but must never arm a
    # fresh stuck-turn timer for an idle session.
    if row.get("running") is False or row.get("terminal_emitted") is True:
        return False
    event = str(row.get("event") or "")
    if event in {"session.info"}:
        return False
    if event == "diagnostic.event":
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        return payload.get("component") in {"turn", "compression"}
    return True


class LedgerTail:
    def __init__(self, path: Path, from_end: bool) -> None:
        self.path = path
        self.offset = 0
        if from_end and path.exists():
            self.offset = path.stat().st_size

    def rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self.offset:
            self.offset = 0
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(self.offset)
                lines = handle.readlines()
                self.offset = handle.tell()
        except OSError:
            return []
        return [row for line in lines if (row := load_json(line))]


def discover_ledgers(home: Path, explicit_ledger: str = "") -> list[Path]:
    if explicit_ledger:
        return [Path(explicit_ledger).expanduser()]
    ledgers = {home / "logs" / "turn-watchdog.jsonl"}
    profiles_dir = home / "profiles"
    if profiles_dir.exists():
        for path in profiles_dir.glob("*/logs/turn-watchdog.jsonl"):
            ledgers.add(path)
    return sorted(ledgers)


def discover_sources(
    home: Path,
    explicit_ledger: str = "",
    *,
    monitor_profile: str = "",
) -> list[Path]:
    """Return privacy-safe runtime ledgers consumed by the live watchdog."""

    if explicit_ledger:
        return discover_ledgers(home, explicit_ledger)
    sources = {
        *discover_ledgers(home),
        home / "logs" / "desktop-events.jsonl",
        home / "logs" / "personal-assistant-monitor.jsonl",
    }
    profiles_dir = home / "profiles"
    if profiles_dir.exists():
        sources.update(profiles_dir.glob("*/logs/personal-assistant-monitor.jsonl"))
    if monitor_profile:
        sources.add(
            profiles_dir
            / monitor_profile
            / "logs"
            / "personal-assistant-monitor.jsonl"
        )
    return sorted(sources)


def build_alert(
    state: TurnState,
    idle: float,
    elapsed: float,
    idle_seconds: float,
    ledger: Path,
) -> dict[str, Any]:
    return {
        "ts": utc_now(),
        "severity": "error",
        "component": "live_watchdog",
        "event": (
            "personal_assistant_turn_stuck"
            if state.personal_assistant
            else "turn_stuck"
        ),
        "message": f"Hermes turn has been silent for {idle:.1f}s",
        "session_id": state.session_id,
        "session_key": state.session_key,
        "cwd": state.cwd,
        "last_event": state.last_event,
        "idle_seconds": round(idle, 3),
        "elapsed_seconds": round(elapsed, 3),
        "threshold_seconds": idle_seconds,
        "compression": state.compression,
        "personal_assistant": state.personal_assistant,
        "ledger": str(ledger),
        "payload": state.payload,
        "processes": process_snapshot(),
    }


def build_incident_alert(row: dict[str, Any], ledger: Path) -> dict[str, Any] | None:
    """Classify terminal failures that should alert without waiting for idle."""

    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    if row.get("component") == "personal_assistant_monitor" and row.get("event") in {
        "connector_failure",
        "dead_letter",
    }:
        event = str(row["event"])
        return {
            "ts": utc_now(),
            "severity": "error",
            "component": "live_watchdog",
            "event": f"personal_assistant_monitor_{event}",
            "message": (
                "Hermes personal-assistant monitor cannot reach FlowState"
                if event == "connector_failure"
                else "Hermes personal-assistant monitor exhausted delivery retries"
            ),
            "source": str(row.get("source") or "unknown")[:64],
            "status": str(row.get("status") or "unknown")[:64],
            "count": max(0, int(row.get("count") or 0)),
            "ledger": str(ledger),
            "processes": process_snapshot(),
        }
    if row.get("component") == "sidebar" and row.get("event") == "project_overview_hidden_sessions":
        desktop_details = row.get("details") if isinstance(row.get("details"), dict) else {}
        hidden_count = desktop_details.get("hidden_count")
        if isinstance(hidden_count, bool) or not isinstance(hidden_count, (int, float)) or hidden_count <= 0:
            return None
        return {
            "ts": utc_now(),
            "severity": "error",
            "component": "live_watchdog",
            "event": "sidebar_sessions_hidden",
            "message": "Hermes Projects view omitted loaded conversations",
            "hidden_count": int(hidden_count),
            "ledger": str(ledger),
            "processes": process_snapshot(),
        }
    searchable = " ".join(
        str(value)
        for value in (
            payload.get("message"),
            payload.get("text"),
            payload.get("error"),
            details.get("error"),
        )
        if value
    )
    if "session not found" not in searchable.lower():
        return None
    # Idempotent cleanup can legitimately race with another client or retry a
    # stale sidebar row. That is not a failed user turn and must not produce a
    # critical desktop recovery notification.
    if payload.get("method") in {"session.delete", "session.close", "session.cancel"}:
        return None
    return {
        "ts": utc_now(),
        "severity": "error",
        "component": "live_watchdog",
        "event": "session_not_found",
        "message": "Hermes attempted to use a missing runtime session",
        "session_id": str(row.get("session_id") or ""),
        "session_key": str(row.get("session_key") or ""),
        "cwd": str(row.get("cwd") or ""),
        "ledger": str(ledger),
        "payload": payload,
        "processes": process_snapshot(),
    }


def monitor_heartbeat_timestamp(row: dict[str, Any]) -> float | None:
    if row.get("component") != "personal_assistant_monitor":
        return None
    try:
        value = str(row.get("ts") or "").replace("Z", "+00:00")
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.timestamp()


def seed_monitor_heartbeats(path: Path) -> dict[str, float]:
    """Read only the latest monitor heartbeats without replaying old incidents."""

    if path.name != "personal-assistant-monitor.jsonl" or not path.is_file():
        return {}
    latest = {"producer": 0.0, "consumer": 0.0}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-1000:]
    except OSError:
        return latest
    for line in lines:
        row = load_json(line)
        if row is None:
            continue
        heartbeat_at = monitor_heartbeat_timestamp(row)
        source = str(row.get("source") or "")
        if heartbeat_at is not None and source in latest:
            latest[source] = max(latest[source], heartbeat_at)
    return latest


def build_monitor_stale_alert(
    *,
    source: str,
    age: float,
    threshold: float,
    ledger: Path,
    heartbeat_seen: bool = True,
) -> dict[str, Any]:
    return {
        "ts": utc_now(),
        "severity": "error",
        "component": "live_watchdog",
        "event": f"personal_assistant_monitor_{source}_stale",
        "message": f"Hermes personal-assistant monitor {source} heartbeat is stale",
        "source": source,
        "age_seconds": round(age, 3),
        "threshold_seconds": round(threshold, 3),
        "heartbeat_seen": heartbeat_seen,
        "ledger": str(ledger),
        "processes": process_snapshot(),
    }


def run(args: argparse.Namespace) -> int:
    home = Path(args.home).expanduser() if args.home else hermes_home()
    alerts = Path(args.alerts).expanduser() if args.alerts else home / "logs" / "live-watchdog-alerts.jsonl"
    latest = alerts.with_suffix(".latest.json")
    states: dict[str, TurnState] = {}
    state_ledgers: dict[str, Path] = {}
    incident_alerted_at: dict[tuple[str, str], float] = {}
    monitor_heartbeats: dict[tuple[Path, str], float] = {}
    monitor_heartbeat_seen: set[tuple[Path, str]] = set()
    tails: dict[Path, LedgerTail] = {}
    last_ledger_refresh = 0.0
    stopped = False
    watchdog_started_at = time.time()
    expected_monitor = (
        home
        / "profiles"
        / args.monitor_profile
        / "logs"
        / "personal-assistant-monitor.jsonl"
        if args.monitor_profile
        else None
    )

    def refresh_ledgers() -> None:
        for path in discover_sources(
            home,
            args.ledger,
            monitor_profile=args.monitor_profile,
        ):
            if path not in tails:
                seeded = seed_monitor_heartbeats(path)
                sources = {"producer", "consumer"} if path == expected_monitor else set(seeded)
                for source in sources:
                    heartbeat_at = seeded.get(source, 0.0)
                    key = (path, source)
                    if heartbeat_at > 0:
                        monitor_heartbeats[key] = heartbeat_at
                        monitor_heartbeat_seen.add(key)
                    elif path == expected_monitor:
                        monitor_heartbeats[key] = 0.0 if path.is_file() else watchdog_started_at
                tails[path] = LedgerTail(path, from_end=args.from_end)

    def stop(_signum, _frame) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    refresh_ledgers()
    watched = ", ".join(str(path) for path in tails) or str(home / "logs" / "turn-watchdog.jsonl")
    append_jsonl(
        alerts,
        {
            "ts": utc_now(),
            "severity": "info",
            "component": "live_watchdog",
            "event": "watchdog_started",
            "message": "Hermes live watchdog started",
            "home": str(home),
            "ledgers": [str(path) for path in tails],
            "idle_seconds": args.idle_seconds,
            "alert_cooldown_seconds": args.alert_cooldown,
        },
    )
    print(
        f"[hermes-live-watchdog] watching {watched} "
        f"(idle>{args.idle_seconds:.1f}s, cooldown>{args.alert_cooldown:.1f}s)",
        flush=True,
    )

    while not stopped:
        now = time.time()
        if now - last_ledger_refresh >= LEDGER_REFRESH_SECONDS:
            refresh_ledgers()
            last_ledger_refresh = now
        for ledger, tail in list(tails.items()):
            for row in tail.rows():
                sid = str(row.get("session_id") or "")
                heartbeat_at = monitor_heartbeat_timestamp(row)
                if heartbeat_at is not None:
                    source = str(row.get("source") or "unknown")[:64]
                    key = (ledger, source)
                    monitor_heartbeats[key] = heartbeat_at
                    monitor_heartbeat_seen.add(key)
                incident = build_incident_alert(row, ledger)
                if incident is not None:
                    incident_key = (sid or "desktop", str(incident["event"]))
                    last_incident = incident_alerted_at.get(incident_key, 0.0)
                    if now - last_incident >= args.alert_cooldown:
                        incident_alerted_at[incident_key] = now
                        append_jsonl(alerts, incident)
                        latest.write_text(
                            json.dumps(incident, ensure_ascii=False, indent=2, sort_keys=True),
                            encoding="utf-8",
                        )
                        if incident["event"] == "sidebar_sessions_hidden":
                            line = (
                                "[hermes-live-watchdog] SIDEBAR_SESSIONS_HIDDEN "
                                f"count={incident['hidden_count']} ledger={ledger}"
                            )
                        elif incident["event"].startswith("personal_assistant_monitor_"):
                            line = (
                                "[hermes-live-watchdog] PERSONAL_ASSISTANT_MONITOR "
                                f"event={incident['event']} status={incident['status']} "
                                f"count={incident['count']} ledger={ledger}"
                            )
                        else:
                            line = (
                                f"[hermes-live-watchdog] SESSION_NOT_FOUND sid={sid[:8]} "
                                f"key={incident['session_key']} ledger={ledger}"
                            )
                        print(line, flush=True)
                        if args.notify:
                            if incident["event"] == "sidebar_sessions_hidden":
                                title = "Hermes conversations may be hidden"
                            elif incident["event"].startswith("personal_assistant_monitor_"):
                                title = "Hermes personal assistant monitor needs attention"
                            else:
                                title = "Hermes session recovery failed"
                            desktop_notify(title, line)
                if not sid:
                    continue
                if is_terminal(row):
                    states.pop(sid, None)
                    state_ledgers.pop(sid, None)
                    continue
                if not is_progress(row):
                    continue
                state = states.get(sid)
                if state is None:
                    state = TurnState(session_id=sid)
                    states[sid] = state
                state_ledgers[sid] = ledger
                state.update(row)

        for (ledger, source), heartbeat_at in list(monitor_heartbeats.items()):
            threshold = (
                args.monitor_producer_stale_seconds
                if source == "producer"
                else args.monitor_consumer_stale_seconds
            )
            age = now - heartbeat_at
            if age < threshold:
                continue
            event_name = f"personal_assistant_monitor_{source}_stale"
            incident_key = (str(ledger), event_name)
            last_incident = incident_alerted_at.get(incident_key, 0.0)
            if now - last_incident < args.alert_cooldown:
                continue
            incident_alerted_at[incident_key] = now
            alert = build_monitor_stale_alert(
                source=source,
                age=age,
                threshold=threshold,
                ledger=ledger,
                heartbeat_seen=(ledger, source) in monitor_heartbeat_seen,
            )
            append_jsonl(alerts, alert)
            latest.write_text(
                json.dumps(alert, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            line = (
                "[hermes-live-watchdog] PERSONAL_ASSISTANT_MONITOR_STALE "
                f"source={source} age={age:.1f}s ledger={ledger}"
            )
            print(line, flush=True)
            if args.notify:
                desktop_notify("Hermes personal assistant monitor is stale", line)

        for sid, state in list(states.items()):
            last = state.last_progress_at or state.started_at
            idle = now - last
            elapsed = now - (state.started_at or last)
            if idle < args.idle_seconds:
                continue
            if now - state.last_alert_at < args.alert_cooldown:
                continue
            state.last_alert_at = now
            ledger = state_ledgers.get(sid) or Path("")
            alert = build_alert(state, idle, elapsed, args.idle_seconds, ledger)
            append_jsonl(alerts, alert)
            latest.write_text(
                json.dumps(alert, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            line = (
                f"[hermes-live-watchdog] STUCK sid={sid[:8]} "
                f"key={state.session_key} idle={idle:.1f}s last={state.last_event} ledger={ledger}"
            )
            print(line, flush=True)
            if args.notify:
                desktop_notify("Hermes turn may be stuck", line)

        if args.once:
            return 0
        time.sleep(args.interval)
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", default="", help="Hermes home, default ~/.hermes or HERMES_HOME")
    parser.add_argument("--ledger", default="", help="Turn watchdog ledger path")
    parser.add_argument("--alerts", default="", help="Alert JSONL path")
    parser.add_argument("--idle-seconds", type=float, default=env_float("HERMES_LIVE_WATCHDOG_IDLE_SECONDS", DEFAULT_IDLE_SECONDS))
    parser.add_argument("--alert-cooldown", type=float, default=env_float("HERMES_LIVE_WATCHDOG_ALERT_COOLDOWN_SECONDS", DEFAULT_ALERT_COOLDOWN_SECONDS))
    parser.add_argument("--monitor-producer-stale-seconds", type=float, default=env_float("HERMES_PA_MONITOR_PRODUCER_STALE_SECONDS", DEFAULT_MONITOR_PRODUCER_STALE_SECONDS))
    parser.add_argument("--monitor-consumer-stale-seconds", type=float, default=env_float("HERMES_PA_MONITOR_CONSUMER_STALE_SECONDS", DEFAULT_MONITOR_CONSUMER_STALE_SECONDS))
    parser.add_argument("--monitor-profile", default=os.environ.get("HERMES_PA_MONITOR_PROFILE", "office-work").strip(), help="Expected personal-assistant monitor profile")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--from-start", dest="from_end", action="store_false", help="Read existing ledger rows first")
    parser.add_argument("--from-end", dest="from_end", action="store_true", help="Only watch new ledger rows")
    parser.set_defaults(from_end=True)
    parser.add_argument("--notify", action="store_true", help="Send desktop notifications with notify-send")
    parser.add_argument("--once", action="store_true", help="Process available rows once and exit")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(run(parse_args(sys.argv[1:])))
