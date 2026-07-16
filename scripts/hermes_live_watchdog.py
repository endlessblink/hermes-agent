#!/usr/bin/env python3
"""Privacy-safe watchdog for the Hermes personal-assistant monitor."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import shlex
import subprocess
import sys
import time
from typing import Any, Iterable


DEFAULT_PRODUCER_STALE_SECONDS = 20 * 60.0
DEFAULT_CONSUMER_STALE_SECONDS = 5 * 60.0
DEFAULT_ALERT_COOLDOWN_SECONDS = 20 * 60.0
MAX_FUTURE_SKEW_SECONDS = 60.0
MONITOR_UNIT = "hermes-personal-assistant-monitor"
LIVENESS_EVENTS = {
    "producer": "producer_check",
    "consumer": "consumer_heartbeat",
}
INCIDENT_EVENTS = {"connector_failure", "dead_letter"}
RESOLUTION_EVENTS = {
    "connector_failure": "producer_check",
    "dead_letter": "dead_letter_resolved",
}


def _safe_slug(value: Any, fallback: str = "unknown") -> str:
    clean = "".join(
        char for char in str(value or "").lower() if char.isalnum() or char in "-_"
    )[:64]
    return clean or fallback


def _safe_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _private_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def _private_append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _timestamp(value: Any, *, now: float) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    result = parsed.timestamp()
    if result < 0 or result > now + MAX_FUTURE_SKEW_SECONDS:
        return None
    return result


def _rows(path: Path) -> Iterable[dict[str, Any]]:
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for line in handle:
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(row, dict):
                yield row


def scan_monitor_ledger(
    path: Path,
    *,
    profile: str,
    now: float,
) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    """Reconstruct liveness and unresolved incidents without a bounded tail."""

    heartbeats: dict[str, dict[str, Any]] = {}
    incident_marks: dict[tuple[str, str], tuple[float, bool, dict[str, Any]]] = {}
    for row in _rows(path):
        if row.get("component") != "personal_assistant_monitor":
            continue
        if _safe_slug(row.get("profile")) != profile:
            continue
        source = _safe_slug(row.get("source"))
        event = _safe_slug(row.get("event"))
        timestamp = _timestamp(row.get("ts"), now=now)
        if source not in LIVENESS_EVENTS or timestamp is None:
            continue
        if event == LIVENESS_EVENTS[source]:
            current = heartbeats.get(source)
            if current is None or timestamp > current["timestamp"]:
                heartbeats[source] = {
                    "timestamp": timestamp,
                    "owner_pid": row.get("owner_pid"),
                    "owner_start_ticks": row.get("owner_start_ticks"),
                }
        for incident_event, resolution_event in RESOLUTION_EVENTS.items():
            if event not in {incident_event, resolution_event}:
                continue
            key = (source, incident_event)
            current = incident_marks.get(key)
            if current is not None and timestamp <= current[0]:
                continue
            incident_marks[key] = (
                timestamp,
                event == resolution_event,
                {
                    "source": source,
                    "event": incident_event,
                    "status": _safe_slug(row.get("status")),
                    "count": _safe_count(row.get("count")),
                    "timestamp": timestamp,
                },
            )
    incidents = {
        key: details
        for key, (_timestamp_value, resolved, details) in incident_marks.items()
        if not resolved
    }
    return heartbeats, incidents


def process_start_ticks(pid: int) -> int | None:
    if platform.system() != "Linux":
        return None
    try:
        raw = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8")
        closing_paren = raw.rfind(")")
        if closing_paren < 0:
            return None
        return int(raw[closing_paren + 1 :].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def _consumer_command_is_gateway(pid: int) -> bool:
    if platform.system() != "Linux":
        return False
    try:
        tokens = [
            token.decode("utf-8", errors="replace")
            for token in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            if token
        ]
    except OSError:
        return False
    modules = {"tui_gateway.entry", "tui_gateway.ws"}
    if any(token in modules for token in tokens):
        return True
    return any(
        token.endswith(("/tui_gateway/entry.py", "/tui_gateway/ws.py"))
        for token in tokens
    )


def consumer_owner_active(heartbeat: dict[str, Any]) -> bool:
    pid = heartbeat.get("owner_pid")
    ticks = heartbeat.get("owner_start_ticks")
    if (
        not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
        or not isinstance(ticks, int)
        or isinstance(ticks, bool)
        or ticks <= 0
    ):
        return False
    return process_start_ticks(pid) == ticks and _consumer_command_is_gateway(pid)


def _systemctl(*args: str) -> subprocess.CompletedProcess[str] | None:
    if platform.system() != "Linux":
        return None
    try:
        return subprocess.run(
            ["systemctl", "--user", *args],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def producer_owner_active(profile_home: Path) -> bool:
    active = _systemctl("is-active", f"{MONITOR_UNIT}.timer")
    if active is None or active.returncode != 0 or active.stdout.strip() != "active":
        return False
    shown = _systemctl("show", f"{MONITOR_UNIT}.service", "--property=ExecStart", "--value")
    if shown is None or shown.returncode != 0:
        return False
    command = shown.stdout.replace("%%", "%")
    try:
        tokens = [token.strip("{};") for token in shlex.split(command)]
        if tokens.count("--profile-home") != 1 or tokens.count("-m") != 1:
            return False
        profile_index = tokens.index("--profile-home")
        configured_profile = tokens[profile_index + 1]
        module_index = tokens.index("-m")
        configured_module = tokens[module_index + 1]
    except (ValueError, IndexError):
        return False
    return (
        configured_module == "agent.personal_assistant_monitor"
        and configured_profile == str(profile_home.resolve())
    )


def _load_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {"version": 1, "alerts": {}}
    alerts = value.get("alerts") if isinstance(value, dict) else None
    return {"version": 1, "alerts": alerts if isinstance(alerts, dict) else {}}


def _alert_key(profile: str, source: str, event: str, identity: str = "") -> str:
    return ":".join((profile, source, event, identity))


def _should_alert(state: dict[str, Any], key: str, now: float, cooldown: float) -> bool:
    try:
        last = float(state["alerts"].get(key, 0))
    except (TypeError, ValueError):
        last = 0
    if now - last < cooldown:
        return False
    state["alerts"][key] = now
    return True


def _safe_alert(
    *,
    profile: str,
    source: str,
    event: str,
    now: float,
    status: str = "attention",
    count: int = 0,
    age_seconds: float | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ts": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "component": "personal_assistant_monitor",
        "profile": profile,
        "source": source,
        "event": event,
        "status": _safe_slug(status, "attention"),
        "count": _safe_count(count),
    }
    if age_seconds is not None:
        row["age_seconds"] = max(0, int(age_seconds))
    return row


def _notify(row: dict[str, Any]) -> None:
    executable = shutil.which("notify-send")
    if not executable:
        return
    source = "FlowState check" if row["source"] == "producer" else "Hermes delivery"
    event = str(row["event"]).replace("_", " ")
    try:
        subprocess.run(
            [
                executable,
                "--app-name=Hermes",
                "Hermes personal assistant needs attention",
                f"{source}: {event}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return


def check(args: argparse.Namespace, *, now: float | None = None) -> list[dict[str, Any]]:
    current = time.time() if now is None else now
    profile_home = Path(args.profile_home).expanduser().resolve()
    profile = _safe_slug(profile_home.name)
    ledger = profile_home / "logs" / "personal-assistant-monitor.jsonl"
    alerts_path = profile_home / "logs" / "personal-assistant-watchdog.jsonl"
    latest_path = profile_home / "state" / "personal-assistant-watchdog" / "latest.json"
    state_path = profile_home / "state" / "personal-assistant-watchdog" / "state.json"
    state = _load_state(state_path)
    heartbeats, incidents = scan_monitor_ledger(
        ledger,
        profile=profile,
        now=current,
    )
    emitted: list[dict[str, Any]] = []
    active_rows: list[dict[str, Any]] = []

    for (source, event), incident in incidents.items():
        identity = f"{incident['timestamp']:.6f}"
        key = _alert_key(profile, source, event, identity)
        row = _safe_alert(
            profile=profile,
            source=source,
            event=event,
            now=current,
            status=incident["status"],
            count=incident["count"],
        )
        active_rows.append(row)
        if _should_alert(state, key, current, args.alert_cooldown_seconds):
            emitted.append(row)

    owners = {
        "producer": producer_owner_active(profile_home),
        "consumer": consumer_owner_active(heartbeats.get("consumer", {})),
    }
    thresholds = {
        "producer": args.producer_stale_seconds,
        "consumer": args.consumer_stale_seconds,
    }
    for source, owner_active in owners.items():
        if not owner_active:
            continue
        heartbeat = heartbeats.get(source)
        age = current - float(heartbeat["timestamp"]) if heartbeat else float("inf")
        if age < thresholds[source]:
            continue
        key = _alert_key(profile, source, "stale")
        row = _safe_alert(
            profile=profile,
            source=source,
            event="stale",
            now=current,
            age_seconds=None if age == float("inf") else age,
        )
        active_rows.append(row)
        if _should_alert(state, key, current, args.alert_cooldown_seconds):
            emitted.append(row)

    for row in emitted:
        _private_append_jsonl(alerts_path, row)
        if args.notify:
            _notify(row)
    if active_rows:
        _private_write_json(latest_path, active_rows[-1])
    else:
        try:
            latest_path.unlink()
        except FileNotFoundError:
            pass
    if len(state["alerts"]) > 512:
        newest = sorted(
            state["alerts"].items(), key=lambda item: float(item[1]), reverse=True
        )[:512]
        state["alerts"] = dict(newest)
    _private_write_json(state_path, state)
    return emitted


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-home", required=True)
    parser.add_argument(
        "--producer-stale-seconds",
        type=float,
        default=DEFAULT_PRODUCER_STALE_SECONDS,
    )
    parser.add_argument(
        "--consumer-stale-seconds",
        type=float,
        default=DEFAULT_CONSUMER_STALE_SECONDS,
    )
    parser.add_argument(
        "--alert-cooldown-seconds",
        type=float,
        default=DEFAULT_ALERT_COOLDOWN_SECONDS,
    )
    parser.add_argument("--notify", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    check(parse_args(sys.argv[1:] if argv is None else argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
