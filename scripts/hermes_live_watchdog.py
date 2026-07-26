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
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import types
from urllib.error import URLError
from urllib.request import urlopen
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_IDLE_SECONDS = 45.0
DEFAULT_ALERT_COOLDOWN_SECONDS = 60.0
DEFAULT_MONITOR_PRODUCER_STALE_SECONDS = 20 * 60.0
DEFAULT_MONITOR_CONSUMER_STALE_SECONDS = 20 * 60.0
LEDGER_REFRESH_SECONDS = 10.0
FLOWSTATE_AUTH_ALERT_BACKOFF_SECONDS = 20 * 60.0
MAX_ALERT_LOG_BYTES = 5 * 1024 * 1024
MAX_ALERT_LOG_BACKUPS = 2
MAX_PROCESS_SNAPSHOT_ROWS = 12
WAIT_REQUEST_EVENTS = frozenset(
    {
        "clarify.request",
        "approval.request",
        "terminal.read.request",
        "sudo.request",
        "secret.request",
        "input.request",
    }
)
WAIT_RESUME_EVENTS = frozenset(
    event.removesuffix(".request") + ".resume" for event in WAIT_REQUEST_EVENTS
)

_INCIDENT_TOKEN_LIMIT = 80
_INCIDENT_COUNT_LIMIT = 1000
_improvement_supervisor_ingester = None
_improvement_supervisor_repair_tick = None
_last_improvement_ingest_error_at = 0.0


def _load_improvement_supervisor_ingester():
    """Lazy-load the plugin's bounded ingester without starting a backend."""
    global _improvement_supervisor_ingester, _improvement_supervisor_repair_tick
    if _improvement_supervisor_ingester is not None:
        return _improvement_supervisor_ingester
    package_name = "hermes_watchdog_plugins.improvement_supervisor"
    plugin_dir = Path(__file__).resolve().parents[1] / "plugins" / "improvement-supervisor"
    module = sys.modules.get(package_name)
    if module is None:
        parent_name = package_name.rsplit(".", 1)[0]
        if parent_name not in sys.modules:
            namespace = types.ModuleType(parent_name)
            namespace.__path__ = []
            sys.modules[parent_name] = namespace
        spec = importlib.util.spec_from_file_location(
            package_name,
            plugin_dir / "__init__.py",
            submodule_search_locations=[str(plugin_dir)],
        )
        if spec is None or spec.loader is None:
            raise ImportError("improvement supervisor plugin is unavailable")
        module = importlib.util.module_from_spec(spec)
        module.__package__ = package_name
        module.__path__ = [str(plugin_dir)]
        sys.modules[package_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(package_name, None)
            raise
    ingester = getattr(module, "ingest_runtime_events_for_root", None)
    if not callable(ingester):
        raise ImportError("improvement supervisor ingester is unavailable")
    repair_tick = getattr(module, "tick_repair_worker_for_root", None)
    if not callable(repair_tick):
        raise ImportError("improvement supervisor repair worker is unavailable")
    _improvement_supervisor_ingester = ingester
    _improvement_supervisor_repair_tick = repair_tick
    return ingester


def _ingest_improvement_supervisor_events(home: Path) -> bool:
    """Run one fail-safe ingestion tick owned by the always-on watchdog."""
    global _last_improvement_ingest_error_at
    try:
        _load_improvement_supervisor_ingester()(home)
        return True
    except Exception as exc:
        now = time.monotonic()
        if now - _last_improvement_ingest_error_at >= 60.0:
            _last_improvement_ingest_error_at = now
            print(
                "[hermes-live-watchdog] improvement ingestion failed safely: "
                f"{type(exc).__name__}",
                file=sys.stderr,
                flush=True,
            )
        return False


def _tick_improvement_supervisor_repair(home: Path) -> str:
    """Run one fail-safe, globally deduplicated repair-worker tick."""
    try:
        _load_improvement_supervisor_ingester()
        if not callable(_improvement_supervisor_repair_tick):
            return "unavailable"
        return str(_improvement_supervisor_repair_tick(home))
    except Exception as exc:
        print(
            "[hermes-live-watchdog] repair worker failed safely: "
            f"{type(exc).__name__}",
            file=sys.stderr,
            flush=True,
        )
        return "failed_safely"


def _incident_token(value: Any, limit: int = _INCIDENT_TOKEN_LIMIT) -> str:
    """Return a bounded machine token, never arbitrary message content."""

    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    raw = str(value).strip()
    safe = "".join(
        character
        for character in raw
        if character.isalnum() or character in "._:/-"
    )
    return safe[:limit]


def _incident_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, min(_INCIDENT_COUNT_LIMIT, parsed))


def _incident_bool(details: dict[str, Any], *names: str) -> bool | None:
    for name in names:
        value = details.get(name)
        if isinstance(value, bool):
            return value
    return None


def _error_category(*values: Any) -> str:
    searchable = " ".join(str(value).lower() for value in values if value)
    if any(
        token in searchable
        for token in (
            "ui_unavailable",
            "background delivery is not available",
            "occluded, unfocused renderer",
            "window is not visible",
        )
    ):
        return "ui_unavailable"
    if any(
        token in searchable
        for token in (
            "filesystem_permission",
            "permission denied",
            "operation not permitted",
        )
    ):
        return "filesystem_permission"
    if any(
        token in searchable
        for token in (
            "policy_blocked",
            "blocked:",
            "requires user approval",
            "requires approval",
            "denied by user",
        )
    ):
        return "policy_blocked"
    if any(token in searchable for token in ("connection", "connreset", "broken pipe")):
        return "connection"
    if any(token in searchable for token in ("timeout", "timed out", "deadline")):
        return "timeout"
    if any(token in searchable for token in ("rate limit", "ratelimit", "429")):
        return "rate_limit"
    if any(token in searchable for token in ("unauthorized", "forbidden", "auth", "401", "403")):
        return "authentication"
    if any(token in searchable for token in ("context", "token limit", "too large")):
        return "context_overflow"
    if any(token in searchable for token in ("validation", "invalid", "schema")):
        return "validation"
    if "session not found" in searchable:
        return "session_missing"
    if any(token in searchable for token in ("cancel", "abort")):
        return "cancelled"
    return "unknown"


def _incident_taxonomy(row: dict[str, Any]) -> tuple[str, str]:
    event = str(row.get("event") or "").lower()
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    component = str(payload.get("component") or row.get("component") or "").lower()
    nested_event = str(payload.get("event") or "").lower()

    if event in WAIT_REQUEST_EVENTS:
        return "user_wait", "user_wait"
    if nested_event == "tool_iteration_exhausted":
        return "tool_iteration_exhausted", "tool"
    if component == "compression" or "compression" in event:
        return "compression_stall", "compression"
    if "queue" in event or component == "queue":
        return "queue_failure", "queue"
    if "reconnect" in event or component == "reconnect":
        return "reconnect_failure", "reconnect"
    if "orphan" in event or "orphan" in nested_event:
        return "orphaned_turn", "orphan"
    if event.startswith("tool.") or component == "tool":
        return "tool_failure", "tool"
    if (
        event.startswith("provider.")
        or component in {"provider", "model", "stream"}
        or nested_event in {"provider_timeout", "stream_timeout"}
    ):
        return "provider_stall", "provider"
    return "runtime_failure", "runtime"


def build_incident_context(
    *,
    row: dict[str, Any] | None = None,
    state: "TurnState | None" = None,
    taxonomy: str = "",
) -> dict[str, Any]:
    """Build a bounded, privacy-safe incident bundle for repair automation."""

    if row is None:
        row = {
            "event": (
                state.last_real_progress_event or state.last_event
                if state
                else ""
            ),
            "payload": (
                state.last_real_progress_payload or state.payload
                if state
                else {}
            ),
        }
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    inferred_taxonomy, phase = _incident_taxonomy(row)
    taxonomy = taxonomy or inferred_taxonomy
    if taxonomy == "tool_error_followed_by_stall":
        phase = "tool"

    severity = "info" if taxonomy == "user_wait" else "error"
    failure_level = "waiting" if taxonomy == "user_wait" else "degraded"
    if taxonomy == "orphaned_turn":
        severity, failure_level = "critical", "failed"

    context: dict[str, Any] = {
        "schema_version": 1,
        "taxonomy": taxonomy,
        "severity": severity,
        "failure_level": failure_level,
        "phase": phase,
        "last_real_progress": {
            "event": _incident_token(row.get("event")) or "unknown"
        },
    }

    tool_name = _incident_token(
        payload.get("name") or payload.get("tool_name") or details.get("tool_name")
    )
    if tool_name:
        context["tool"] = {"name": tool_name}

    error_type = _incident_token(details.get("error_type") or payload.get("error_type"))
    status = details.get("status", payload.get("status"))
    status_code = status if isinstance(status, int) or str(status).isdigit() else None
    error_code = _incident_token(
        details.get("error_code") or details.get("code") or status_code
    )
    raw_error = (
        payload.get("error")
        or details.get("error")
        or payload.get("message")
    )
    declared_category = _incident_token(payload.get("error_category"), 32)
    if (
        raw_error
        or error_type
        or error_code
        or declared_category
        or payload.get("status") == "error"
        or ".error" in str(row.get("event") or "")
    ):
        error = {
            "category": _error_category(
                declared_category, raw_error, error_type, error_code
            ),
        }
        if error_code:
            error["code"] = error_code
        if error_type:
            error["type"] = error_type
        context["error"] = error

    retry_attempt = _incident_int(
        details.get(
            "retry_count",
            details.get(
                "retry_attempt",
                details.get("attempt", details.get("iteration_count")),
            ),
        )
    )
    retry_max = _incident_int(
        details.get(
            "max_retries",
            details.get("max_attempts", details.get("iteration_limit")),
        )
    )
    retry: dict[str, Any] = {}
    if retry_attempt is not None:
        retry["attempt"] = retry_attempt
    if retry_max is not None:
        retry["max_attempts"] = retry_max
    if retry:
        context["retry"] = retry

    cancel_requested = _incident_bool(details, "cancel_requested")
    cancel_acknowledged = _incident_bool(details, "cancel_acknowledged")
    cancel: dict[str, Any] = {}
    if cancel_requested is not None:
        cancel["requested"] = cancel_requested
    if cancel_acknowledged is not None:
        cancel["acknowledged"] = cancel_acknowledged
    if cancel:
        context["cancel"] = cancel

    queue_pending = _incident_bool(details, "queue_pending")
    queue_durable = _incident_bool(details, "queued_turn_durable", "queue_durable")
    queue_count = _incident_int(details.get("queue_count"))
    queue: dict[str, Any] = {}
    if queue_durable is not None:
        queue["durable"] = queue_durable
    if queue_pending is not None:
        queue["pending"] = queue_pending
    if queue_count is not None:
        queue["count"] = queue_count
    if queue:
        context["queue"] = queue

    persistence: dict[str, Any] = {}
    for source, target in (("persisted", "persisted"), ("resume_pending", "resume_pending")):
        value = _incident_bool(details, source)
        if value is not None:
            persistence[target] = value
    if persistence:
        context["persistence"] = persistence

    build: dict[str, Any] = {}
    build_id = _incident_token(row.get("runtime_build_id"), 80)
    source_manifest_digest = str(row.get("source_manifest_digest") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", source_manifest_digest):
        source_manifest_digest = ""
    commit = _incident_token(details.get("build_commit"), 12)
    version = _incident_token(details.get("build_version"), 40)
    if build_id:
        build["id"] = build_id
    if source_manifest_digest:
        build["source_manifest_digest"] = source_manifest_digest
    if commit:
        build["commit"] = commit
    if version:
        build["version"] = version
    if build:
        context["build"] = build

    runtime_value = details.get("runtime")
    if isinstance(runtime_value, dict):
        runtime_surface = _incident_token(runtime_value.get("surface"))
    else:
        runtime_surface = _incident_token(runtime_value)
    if runtime_surface:
        context["runtime"] = {"surface": runtime_surface}

    signature_basis = {
        "taxonomy": context["taxonomy"],
        "phase": context["phase"],
        "tool": context.get("tool", {}),
        "error": context.get("error", {}),
    }
    encoded = json.dumps(signature_basis, sort_keys=True, separators=(",", ":"))
    context["signature"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
    return context


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


def write_status_snapshot(path: Path, row: dict[str, Any]) -> None:
    """Atomically publish a bounded heartbeat for user-visible health."""

    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.tmp")
    pending.write_text(
        json.dumps(row, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    pending.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def append_alert_jsonl(path: Path, row: dict[str, Any]) -> None:
    """Append one alert while keeping the live alert history disk-bounded."""

    encoded = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
    encoded_size = len(encoded.encode("utf-8"))
    if encoded_size > MAX_ALERT_LOG_BYTES:
        envelope = {
            key: row[key]
            for key in ("ts", "severity", "component", "event")
            if key in row
        }
        envelope["evidence_truncated"] = True
        incident = row.get("incident")
        if isinstance(incident, dict):
            envelope["incident"] = incident
        encoded = json.dumps(envelope, ensure_ascii=False, sort_keys=True) + "\n"
        if len(encoded.encode("utf-8")) > MAX_ALERT_LOG_BYTES:
            envelope.pop("incident", None)
            encoded = json.dumps(envelope, ensure_ascii=False, sort_keys=True) + "\n"
        encoded_size = len(encoded.encode("utf-8"))
    try:
        current_size = path.stat().st_size
    except OSError:
        current_size = 0
    if current_size and current_size + encoded_size > MAX_ALERT_LOG_BYTES:
        for index in range(MAX_ALERT_LOG_BACKUPS, 0, -1):
            source = path if index == 1 else path.with_name(f"{path.name}.{index - 1}")
            destination = path.with_name(f"{path.name}.{index}")
            if source.exists():
                try:
                    source_size = source.stat().st_size
                except OSError:
                    continue
                if source_size > MAX_ALERT_LOG_BYTES:
                    source.unlink(missing_ok=True)
                else:
                    os.replace(source, destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(encoded)


def flowstate_config_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "flow-state" / "local-api.json"


def flowstate_health_ok(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as response:
            return response.status == 200
    except (OSError, URLError, ValueError):
        return False


def flowstate_app_running() -> bool:
    try:
        result = subprocess.run(
            ["ps", "-eo", "stat=,args="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except Exception:
        return True
    if result.returncode != 0:
        return False
    for raw_line in result.stdout.splitlines():
        parts = raw_line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        state, command = parts
        if state.startswith("Z") or "--type=" in command:
            continue
        if "FlowState.AppImage" in command:
            return True
        if "/tmp/.mount_FlowSt" in command and "/flowstate" in command:
            return True
    return False


def _current_xauthority(env: dict[str, str]) -> str | None:
    configured = env.get("XAUTHORITY")
    if configured and Path(configured).is_file():
        return configured
    runtime_dir = Path(env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")  # windows-footgun: ok — Linux-only watchdog (X11/XDG paths throughout)
    try:
        candidates = [path for path in runtime_dir.glob("xauth_*") if path.is_file()]
        return str(max(candidates, key=lambda path: path.stat().st_mtime)) if candidates else None
    except OSError:
        return None


def launch_flowstate_app() -> bool:
    bin_dir = Path.home() / ".local" / "bin"
    wrapper = bin_dir / "FlowState-launch.sh"
    executable = bin_dir / "FlowState.AppImage"
    if wrapper.is_file() and os.access(wrapper, os.X_OK):
        command = [str(wrapper)]
    elif executable.is_file() and os.access(executable, os.X_OK):
        command = [str(executable), "--no-sandbox", "--ozone-platform=x11", "--disable-gpu"]
    else:
        return False
    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    xauthority = _current_xauthority(env)
    if xauthority:
        env["XAUTHORITY"] = xauthority
    try:
        subprocess.Popen(
            command,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        return False
    return True


def restart_flowstate_app() -> bool:
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,stat=,args="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except Exception:
        return False
    if result.returncode != 0:
        return False

    primary_pids: list[int] = []
    for raw_line in result.stdout.splitlines():
        parts = raw_line.strip().split(maxsplit=2)
        if len(parts) != 3:
            continue
        raw_pid, state, command = parts
        if state.startswith("Z") or "--type=" in command:
            continue
        if "FlowState.AppImage" not in command and not (
            "/tmp/.mount_FlowSt" in command and "/flowstate" in command
        ):
            continue
        try:
            primary_pids.append(int(raw_pid))
        except ValueError:
            continue

    for process_id in primary_pids:
        try:
            os.kill(process_id, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except OSError:
            return False

    deadline = time.monotonic() + 4
    while primary_pids and time.monotonic() < deadline:
        remaining: list[int] = []
        for process_id in primary_pids:
            try:
                os.kill(process_id, 0)
            except ProcessLookupError:
                continue
            except OSError:
                continue
            remaining.append(process_id)
        primary_pids = remaining
        if primary_pids:
            time.sleep(0.1)

    for process_id in primary_pids:
        try:
            os.kill(process_id, signal.SIGKILL)
        except ProcessLookupError:
            continue
        except OSError:
            return False
    return launch_flowstate_app()


def classify_flowstate_recovery(
    *,
    status: str,
    health_ok: bool,
    config: dict[str, Any],
    app_running: bool,
) -> dict[str, str]:
    """Choose only allowlisted repairs; authentication and live restarts fail closed."""

    if status in {"not_signed_in", "signed_out"}:
        return {
            "action": "none",
            "outcome": "auth_required",
            "reason": "flowstate_sign_in_required",
        }
    if health_ok:
        return {
            "action": "none",
            "outcome": "already_healthy",
            "reason": "flowstate_health_recovered",
        }
    port = config.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not (1 <= port <= 65535):
        return {
            "action": "none",
            "outcome": "manual_required",
            "reason": "flowstate_config_invalid",
        }
    if app_running:
        return {
            "action": "restart",
            "outcome": "repair_started",
            "reason": "flowstate_running_but_unhealthy",
        }
    if config.get("enabled") is False:
        return {
            "action": "enable_and_launch",
            "outcome": "repair_started",
            "reason": "flowstate_local_api_disabled",
        }
    if config.get("enabled") is True:
        return {
            "action": "launch",
            "outcome": "repair_started",
            "reason": "flowstate_app_absent",
        }
    return {
        "action": "none",
        "outcome": "manual_required",
        "reason": "flowstate_config_invalid",
    }


def _write_flowstate_config(path: Path, config: dict[str, Any]) -> bool:
    try:
        mode = path.stat().st_mode & 0o777
        temporary = path.with_suffix(path.suffix + ".hermes-repair")
        temporary.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except OSError:
        return False
    return True


def _record_flowstate_recovery_event(
    home: Path,
    profile: str,
    result: dict[str, Any],
) -> None:
    safe = {
        "ts": utc_now(),
        "component": "improvement_supervisor",
        "event": "flowstate_connector_recovery",
        "profile": str(profile)[:80],
        "action": str(result.get("action") or "none")[:80],
        "outcome": str(result.get("outcome") or "unknown")[:80],
        "reason": str(result.get("reason") or "unknown")[:120],
    }
    safe["event_id"] = hashlib.sha256(
        json.dumps(safe, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]
    path = (
        home
        / "profiles"
        / profile
        / "state"
        / "improvement-supervisor"
        / "runtime-events.jsonl"
    )
    append_jsonl(path, safe)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _record_restart_recovery_event(home: Path, profile: str) -> None:
    result = {
        "action": "replay",
        "outcome": "repaired",
        "reason": "durable_pending_turn_matched",
    }
    safe = {
        "ts": utc_now(),
        "component": "improvement_supervisor",
        "event": "restart_interrupted_turn_replayed",
        "profile": str(profile)[:80],
        **result,
    }
    safe["event_id"] = hashlib.sha256(
        json.dumps(safe, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]
    path = (
        home
        / "profiles"
        / profile
        / "state"
        / "improvement-supervisor"
        / "runtime-events.jsonl"
    )
    append_jsonl(path, safe)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _record_turn_timeout_recovery_event(
    home: Path, profile: str, *, reason: str = "turn_idle_timeout"
) -> None:
    safe = {
        "ts": utc_now(),
        "component": "improvement_supervisor",
        "event": "stuck_turn_automatically_stopped",
        "profile": str(profile)[:80],
        "action": "interrupt",
        # Interrupting contains the frozen UI, but it does not prove that the
        # user's task completed. Only a later successful continuation may be
        # reported as repaired.
        "outcome": "contained",
        "reason": str(reason)[:120],
    }
    safe["event_id"] = hashlib.sha256(
        json.dumps(safe, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]
    path = (
        home
        / "profiles"
        / profile
        / "state"
        / "improvement-supervisor"
        / "runtime-events.jsonl"
    )
    append_jsonl(path, safe)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _incident_repo_root(cwd: str) -> str:
    candidates = []
    candidate = Path(cwd) if cwd else Path()
    if candidate.is_absolute():
        candidates.append(candidate)
    candidates.extend((Path(__file__).resolve().parents[1], Path.cwd()))
    for candidate in candidates:
        for path in (candidate, *candidate.parents):
            if (path / ".git").exists():
                return str(path)[:500]
    return ""


def _record_watchdog_incident(
    home: Path,
    profile: str,
    alert: dict[str, Any],
    *,
    state: "TurnState | None" = None,
    row: dict[str, Any] | None = None,
) -> None:
    """Write one fixed-schema incident for the improvement supervisor feeder."""

    context = alert.get("incident")
    if not isinstance(context, dict):
        return
    taxonomy = _incident_token(context.get("taxonomy"), 120)
    severity = _incident_token(context.get("severity"), 16)
    phase = _incident_token(context.get("phase"), 80)
    signature = _incident_token(context.get("signature"), 24)
    if not taxonomy or severity not in {"info", "warning", "error", "critical"}:
        return

    source_payload = row if isinstance(row, dict) else {}
    payload = (
        source_payload.get("payload")
        if isinstance(source_payload.get("payload"), dict)
        else state.last_real_progress_payload
        if state
        else {}
    )
    cwd = str(source_payload.get("cwd") or (state.cwd if state else ""))
    repo_root = _incident_repo_root(cwd)
    build = context.get("build") if isinstance(context.get("build"), dict) else {}
    tool_context = context.get("tool") if isinstance(context.get("tool"), dict) else {}
    queue_context = context.get("queue") if isinstance(context.get("queue"), dict) else {}
    persistence_context = (
        context.get("persistence")
        if isinstance(context.get("persistence"), dict)
        else {}
    )
    retry_context = (
        context.get("retry") if isinstance(context.get("retry"), dict) else {}
    )

    started_at = float(
        source_payload.get("turn_started_at")
        or (state.started_at if state else 0)
        or 0
    )
    last_progress_at = float(
        source_payload.get("turn_last_progress_at")
        or (state.last_progress_at if state else 0)
        or 0
    )
    occurrence = {
        "signature": signature,
        "code": str(alert.get("event") or "")[:120],
        "profile": _incident_token(profile, 80),
        "started_at": started_at,
        "last_progress_at": last_progress_at,
        "ledger_time": float(source_payload.get("monotonic") or 0),
    }
    event_id = hashlib.sha256(
        json.dumps(occurrence, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]

    duration_ms = payload.get("duration_ms")
    duration_seconds = (
        round(float(duration_ms) / 1000, 3)
        if isinstance(duration_ms, (int, float)) and not isinstance(duration_ms, bool)
        else 0
    )
    queue_pending = queue_context.get("pending") is True
    queue_durable = queue_context.get("durable") is True
    persisted = persistence_context.get("persisted") is True
    resume_pending = persistence_context.get("resume_pending") is True
    backend_health = "orphaned" if taxonomy == "orphaned_turn" else "stalled"
    safe = {
        "schema_version": 1,
        "event_id": event_id,
        "event": "watchdog_incident",
        "observed_at": utc_now(),
        "severity": severity,
        "profile": _incident_token(profile, 80),
        "failure": {
            "taxonomy": taxonomy,
            "component": phase or "runtime",
            "code": _incident_token(alert.get("event"), 120) or "watchdog_failure",
            "message": f"Hermes watchdog observed {taxonomy}"[:500],
        },
        "source": {
            "repo_root": repo_root,
            "revision": _incident_token(build.get("commit"), 120) or "unknown",
            "runtime_build_id": _incident_token(build.get("id"), 80),
            "source_manifest_digest": _incident_token(
                build.get("source_manifest_digest"), 64
            ),
        },
        "conversation": {
            "phase": phase or "runtime",
            "started_at": started_at,
            "last_progress_at": last_progress_at,
            "idle_seconds": max(0, min(86400, float(alert.get("idle_seconds") or 0))),
            "waiting": bool(state.waiting) if state else False,
            "compression": bool(state.compression) if state else phase == "compression",
        },
        "tool": {
            "name": _incident_token(tool_context.get("name"), 80),
            "duration_seconds": duration_seconds,
            "status": "error" if context.get("error") else "unknown",
            "attempt": _incident_int(retry_context.get("attempt")) or 0,
        },
        "queue": {
            "depth": _incident_int(queue_context.get("count")) or 0,
            "state": "pending" if queue_pending else "durable" if queue_durable else "unknown",
        },
        "persistence": {
            "revision": _incident_token(build.get("commit"), 120) or "unknown",
            "pending_turn": resume_pending or queue_pending,
            "write_status": "persisted" if persisted else "unknown",
            "read_status": "pending" if resume_pending else "unknown",
        },
        "reconnect": {
            "state": "failed" if phase == "reconnect" else "unknown",
            "attempt": _incident_int(retry_context.get("attempt")) or 0,
        },
        "renderer": {},
        "backend": {
            "pid": state.producer_pid if state else 0,
            "start_ticks": state.producer_start_ticks if state else 0,
            "health": backend_health,
            "session_exists": alert.get("event") != "session_not_found",
            "runtime_revision": _incident_token(build.get("commit"), 120) or "unknown",
        },
        "retry_history": (
            [
                {
                    "attempt": _incident_int(retry_context.get("attempt")) or 0,
                    "classification": _incident_token(
                        (context.get("error") or {}).get("category"), 80
                    ),
                    "outcome": "failed",
                }
            ]
            if retry_context
            else []
        ),
        "logs": [],
    }
    path = (
        home
        / "profiles"
        / safe["profile"]
        / "state"
        / "improvement-supervisor"
        / "runtime-events.jsonl"
    )
    append_jsonl(path, safe)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def attempt_flowstate_recovery(
    *,
    home: Path,
    profile: str,
    status: str,
    verify_attempts: int = 5,
) -> dict[str, str]:
    config_path = flowstate_config_path()
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    port = config.get("port") if isinstance(config.get("port"), int) else 5577
    decision = classify_flowstate_recovery(
        status=str(status or ""),
        health_ok=flowstate_health_ok(port),
        config=config,
        app_running=flowstate_app_running(),
    )
    action = decision["action"]
    if action == "enable_and_launch":
        repaired_config = dict(config)
        repaired_config["enabled"] = True
        if not _write_flowstate_config(config_path, repaired_config):
            decision = {
                "action": action,
                "outcome": "repair_failed",
                "reason": "flowstate_config_write_failed",
            }
        elif not launch_flowstate_app():
            decision = {
                "action": action,
                "outcome": "repair_failed",
                "reason": "flowstate_launch_failed",
            }
    elif action == "launch" and not launch_flowstate_app():
        decision = {
            "action": action,
            "outcome": "repair_failed",
            "reason": "flowstate_launch_failed",
        }
    elif action == "restart" and not restart_flowstate_app():
        decision = {
            "action": action,
            "outcome": "repair_failed",
            "reason": "flowstate_restart_failed",
        }
    if decision["outcome"] == "repair_started":
        for _ in range(max(1, verify_attempts)):
            if flowstate_health_ok(port):
                decision = {
                    "action": action,
                    "outcome": "repaired",
                    "reason": "flowstate_health_verified",
                }
                break
            time.sleep(1)
        else:
            decision = {
                "action": action,
                "outcome": "repair_failed",
                "reason": "flowstate_health_verification_failed",
            }
    _record_flowstate_recovery_event(home, profile, decision)
    return decision


def profile_for_monitor_ledger(home: Path, ledger: Path, fallback: str) -> str:
    try:
        relative = ledger.relative_to(home / "profiles")
    except ValueError:
        return fallback
    return relative.parts[0] if len(relative.parts) >= 3 else fallback


def process_snapshot() -> list[dict[str, Any]]:
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
    snapshot: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        executable = _incident_token(Path(parts[1]).name, 64)
        if not executable:
            continue
        snapshot.append({"pid": pid, "executable": executable})
        if len(snapshot) >= MAX_PROCESS_SNAPSHOT_ROWS:
            break
    return snapshot


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
    turn_id: str = ""
    session_key: str = ""
    cwd: str = ""
    started_at: float = 0.0
    last_progress_at: float = 0.0
    last_event: str = ""
    last_real_progress_event: str = ""
    last_real_progress_payload: dict[str, Any] = field(default_factory=dict)
    compression: bool = False
    personal_assistant: bool = False
    payload: dict[str, Any] = field(default_factory=dict)
    last_alert_at: float = 0.0
    waiting: bool = False
    producer_pid: int = 0
    producer_start_ticks: int = 0

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
        nested_event = str(self.payload.get("event") or "")
        is_heartbeat = self.last_event.endswith(".heartbeat") or (
            self.last_event == "diagnostic.event" and nested_event == "heartbeat"
        )
        if self.last_event != "session.info" and not is_heartbeat:
            self.last_real_progress_event = self.last_event
            self.last_real_progress_payload = self.payload
        if not self.started_at:
            self.started_at = float(row.get("turn_started_at") or now)
        self.compression = bool(row.get("compression_started_at")) or (
            self.payload.get("component") == "compression"
            and self.payload.get("event") in {"start", "heartbeat"}
        )
        self.personal_assistant = bool(
            row.get("personal_assistant", self.personal_assistant)
        )
        self.turn_id = str(row.get("turn_id") or self.turn_id)
        try:
            self.producer_pid = int(row.get("producer_pid") or self.producer_pid)
            self.producer_start_ticks = int(
                row.get("producer_start_ticks") or self.producer_start_ticks
            )
        except (TypeError, ValueError):
            pass
        if self.last_event in WAIT_REQUEST_EVENTS:
            self.waiting = True
        elif self.last_event in WAIT_RESUME_EVENTS:
            self.waiting = False


def turn_state_key(ledger: Path, row: dict[str, Any]) -> tuple[str, str, str]:
    """Identify one turn without colliding across profiles or session reuse."""

    sid = str(row.get("session_id") or "")
    turn_id = str(row.get("turn_id") or "")
    if not turn_id:
        turn_id = f"legacy:{row.get('turn_started_at') or ''}"
    return (str(ledger), sid, turn_id)


def process_start_ticks(pid: int) -> int | None:
    """Return Linux process start ticks, guarding against recycled PIDs."""

    if pid <= 0:
        return None
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
        return int(fields[21])
    except (OSError, ValueError, IndexError):
        return None


def producer_identity_alive(pid: int, expected_start_ticks: int) -> bool:
    if pid <= 0:
        return False
    current = process_start_ticks(pid)
    if current is None:
        return False
    return expected_start_ticks <= 0 or current == expected_start_ticks


def is_terminal(row: dict[str, Any]) -> bool:
    event = str(row.get("event") or "")
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    if event == "session.info" and row.get("running") is False:
        return True
    if event in {"message.complete", "rpc.error"}:
        return True
    if event == "diagnostic.event" and payload.get("component") == "turn":
        return payload.get("event") in {
            "complete",
            "error",
            "finally",
            "idle_timeout",
            "tool_iteration_exhausted",
        }
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
    last_real_event = state.last_real_progress_event or state.last_event
    last_real_payload = state.last_real_progress_payload or state.payload
    taxonomy = ""
    if last_real_event == "tool.error" or (
        last_real_event == "tool.complete" and last_real_payload.get("status") == "error"
    ):
        taxonomy = "tool_error_followed_by_stall"
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
        "incident": build_incident_context(state=state, taxonomy=taxonomy),
    }


def build_orphan_alert(state: TurnState, ledger: Path) -> dict[str, Any]:
    return {
        "ts": utc_now(),
        "severity": "error",
        "component": "live_watchdog",
        "event": "turn_orphaned",
        "message": "Hermes turn producer exited before the turn completed",
        "session_id": state.session_id,
        "session_key": state.session_key,
        "turn_id": state.turn_id,
        "last_event": state.last_event,
        "waiting": state.waiting,
        "ledger": str(ledger),
        "processes": process_snapshot(),
        "incident": build_incident_context(state=state, taxonomy="orphaned_turn"),
    }


def _classify_incident_alert(row: dict[str, Any], ledger: Path) -> dict[str, Any] | None:
    """Classify terminal failures that should alert without waiting for idle."""

    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    if (
        row.get("event") == "diagnostic.event"
        and payload.get("component") == "turn"
        and payload.get("event") == "tool_iteration_exhausted"
    ):
        return {
            "ts": utc_now(),
            "severity": "error",
            "component": "live_watchdog",
            "event": "tool_iteration_exhausted",
            "message": "Hermes exhausted the tool iteration budget",
            "iteration_count": _incident_int(details.get("iteration_count")) or 0,
            "iteration_limit": _incident_int(details.get("iteration_limit")) or 0,
            "ledger": str(ledger),
        }
    if (
        row.get("event") == "diagnostic.event"
        and payload.get("component") == "turn"
        and payload.get("event") == "idle_timeout"
    ):
        return {
            "ts": utc_now(),
            "severity": "info",
            "component": "live_watchdog",
            "event": "stuck_turn_automatically_stopped",
            "message": "Hermes safely stopped a silent turn so the chat can continue",
            "timeout_seconds": float(details.get("timeout_seconds") or 0),
            "last_progress_event": str(details.get("last_progress_event") or "")[:80],
            "ledger": str(ledger),
        }
    if (
        row.get("event") == "diagnostic.event"
        and payload.get("component") == "compression"
        and payload.get("event") == "timeout"
    ):
        return {
            "ts": utc_now(),
            "severity": "info",
            "component": "live_watchdog",
            "event": "stuck_turn_automatically_stopped",
            "message": "Hermes safely stopped stalled context compression",
            "timeout_seconds": float(details.get("timeout_seconds") or 0),
            "last_progress_event": "compression",
            "recovery_reason": "compression_timeout",
            "ledger": str(ledger),
        }
    if (
        row.get("event") == "diagnostic.event"
        and payload.get("component") == "turn"
        and payload.get("event") == "orphan_recovery_started"
    ):
        ordinal = details.get("user_ordinal")
        return {
            "ts": utc_now(),
            "severity": "info",
            "component": "live_watchdog",
            "event": "restart_interrupted_turn_replayed",
            "message": "Hermes safely replayed a restart-interrupted turn",
            "user_ordinal": int(ordinal) if isinstance(ordinal, int) else -1,
            "ledger": str(ledger),
        }
    if row.get("component") == "personal_assistant_monitor" and row.get("event") in {
        "connector_failure",
        "dead_letter",
    }:
        event = str(row["event"])
        status = str(row.get("status") or "unknown")[:64]
        auth_required = status in {"not_signed_in", "signed_out"}
        return {
            "ts": utc_now(),
            "severity": "error",
            "component": "live_watchdog",
            "event": f"personal_assistant_monitor_{event}",
            "message": (
                "Sign in to FlowState to resume personal-assistant monitoring"
                if auth_required
                else "Hermes personal-assistant monitor cannot reach FlowState"
                if event == "connector_failure"
                else "Hermes personal-assistant monitor exhausted delivery retries"
            ),
            "source": str(row.get("source") or "unknown")[:64],
            "status": status,
            "action_required": "flowstate_sign_in" if auth_required else "inspect_connector",
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
    if row.get("component") == "composer" and row.get("event") == "queue.action_mismatch":
        desktop_details = row.get("details") if isinstance(row.get("details"), dict) else {}
        presented_action = _incident_token(desktop_details.get("presented_action"), 20)
        actual_action = _incident_token(desktop_details.get("actual_action"), 20)
        if not presented_action or not actual_action or presented_action == actual_action:
            return None
        return {
            "ts": utc_now(),
            "severity": "error",
            "component": "live_watchdog",
            "event": "composer_action_mismatch",
            "message": "Hermes composer performed a different action than it presented",
            "presented_action": presented_action,
            "actual_action": actual_action,
            "ledger": str(ledger),
        }
    event = str(row.get("event") or "")
    tool_failed = event == "tool.error" or (
        event.startswith("tool.") and payload.get("status") == "error"
    )
    if tool_failed:
        tool_name = _incident_token(payload.get("name"), 80) or "unknown"
        error_category = _error_category(
            payload.get("error"),
            payload.get("error_category"),
            details.get("error"),
            details.get("error_type"),
        )
        return {
            "ts": utc_now(),
            "severity": "error",
            "component": "live_watchdog",
            "event": "tool_failure",
            "message": "Hermes tool call failure detected",
            "tool": tool_name,
            "error_category": error_category,
            "ledger": str(ledger),
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
    if payload.get("method") in {
        "session.delete",
        "session.close",
        "session.cancel",
        "session.usage",
        "process.list",
    }:
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


def build_incident_alert(row: dict[str, Any], ledger: Path) -> dict[str, Any] | None:
    alert = _classify_incident_alert(row, ledger)
    if alert is None:
        return None
    alert["incident"] = build_incident_context(row=row)
    return alert


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


def monitor_stale_alert_cooldown(args: argparse.Namespace, source: str) -> float:
    stale_window = (
        args.monitor_producer_stale_seconds
        if source == "producer"
        else args.monitor_consumer_stale_seconds
    )
    return max(args.alert_cooldown, stale_window)


def incident_cooldown_seconds(
    args: argparse.Namespace, incident: dict[str, Any]
) -> float:
    if (
        incident.get("event") == "personal_assistant_monitor_connector_failure"
        and incident.get("status") in {"not_signed_in", "signed_out"}
    ):
        return max(args.alert_cooldown, FLOWSTATE_AUTH_ALERT_BACKOFF_SECONDS)
    return args.alert_cooldown


def _tool_result_key(row: dict[str, Any], ledger: Path) -> tuple[str, str, str] | None:
    event = str(row.get("event") or "")
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    if not event.startswith("tool.") or payload.get("status") not in {"error", "complete"}:
        return None
    tool = _incident_token(payload.get("name"), 80)
    session = _incident_token(
        row.get("session_id") or row.get("session_key"),
        160,
    )
    if not tool or not session:
        return None
    return str(ledger), session, tool


def run(args: argparse.Namespace) -> int:
    home = Path(args.home).expanduser() if args.home else hermes_home()
    alerts = Path(args.alerts).expanduser() if args.alerts else home / "logs" / "live-watchdog-alerts.jsonl"
    latest = alerts.with_suffix(".latest.json")
    states: dict[tuple[str, str, str], TurnState] = {}
    state_ledgers: dict[tuple[str, str, str], Path] = {}
    incident_alerted_at: dict[tuple[str, str], float] = {}
    pending_tool_failures: dict[tuple[str, str, str], dict[str, Any]] = {}
    monitor_heartbeats: dict[tuple[Path, str], float] = {}
    monitor_heartbeat_seen: set[tuple[Path, str]] = set()
    tails: dict[Path, LedgerTail] = {}
    last_ledger_refresh = 0.0
    stopped = False
    watchdog_started_at = time.time()
    watchdog_started_iso = utc_now()
    status_path = home / "logs" / "live-watchdog-status.json"
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
    append_alert_jsonl(
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

    def write_heartbeat() -> None:
        write_status_snapshot(
            status_path,
            {
                "state": "running",
                "startedAt": watchdog_started_iso,
                "heartbeatAt": utc_now(),
                "watchedSources": len(tails),
            },
        )

    write_heartbeat()
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
                tool_result_key = _tool_result_key(row, ledger)
                row_payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                if incident is not None and incident.get("event") == "tool_failure" and tool_result_key:
                    pending_tool_failures[tool_result_key] = {
                        "error_category": incident.get("error_category") or "unknown",
                    }
                elif (
                    incident is None
                    and tool_result_key
                    and row_payload.get("status") == "complete"
                    and tool_result_key in pending_tool_failures
                ):
                    failure = pending_tool_failures.pop(tool_result_key)
                    incident = {
                        "ts": utc_now(),
                        "severity": "info",
                        "component": "live_watchdog",
                        "event": "tool_recovered",
                        "message": "Hermes tool retry succeeded after a recorded failure",
                        "tool": tool_result_key[2],
                        "error_category": failure["error_category"],
                        "recovery_outcome": "verified_retry_succeeded",
                        "ledger": str(ledger),
                    }
                if incident is not None:
                    context = incident.get("incident")
                    signature = (
                        str(context.get("signature") or "")
                        if isinstance(context, dict)
                        else ""
                    )
                    incident_key = (
                        "signature",
                        signature,
                    ) if signature else (sid or "desktop", str(incident["event"]))
                    last_incident = incident_alerted_at.get(incident_key, 0.0)
                    if now - last_incident >= incident_cooldown_seconds(args, incident):
                        incident_alerted_at[incident_key] = now
                        if incident["event"] == "personal_assistant_monitor_connector_failure":
                            recovery = attempt_flowstate_recovery(
                                home=home,
                                profile=profile_for_monitor_ledger(
                                    home,
                                    ledger,
                                    args.monitor_profile,
                                ),
                                status=str(incident.get("status") or ""),
                            )
                            incident["recovery_action"] = recovery["action"]
                            incident["recovery_outcome"] = recovery["outcome"]
                            incident["recovery_reason"] = recovery["reason"]
                        elif incident["event"] == "restart_interrupted_turn_replayed":
                            _record_restart_recovery_event(
                                home,
                                profile_for_monitor_ledger(
                                    home,
                                    ledger,
                                    args.monitor_profile,
                                ),
                            )
                        elif incident["event"] == "stuck_turn_automatically_stopped":
                            _record_turn_timeout_recovery_event(
                                home,
                                profile_for_monitor_ledger(
                                    home,
                                    ledger,
                                    args.monitor_profile,
                                ),
                                reason=str(
                                    incident.get("recovery_reason")
                                    or "turn_idle_timeout"
                                ),
                            )
                        append_alert_jsonl(alerts, incident)
                        if incident.get("severity") in {"error", "critical"}:
                            _record_watchdog_incident(
                                home,
                                profile_for_monitor_ledger(
                                    home,
                                    ledger,
                                    args.monitor_profile,
                                ),
                                incident,
                                row=row,
                            )
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
                        elif incident["event"] == "stuck_turn_automatically_stopped":
                            line = (
                                "[hermes-live-watchdog] STUCK_TURN_AUTOMATICALLY_STOPPED "
                                f"timeout={incident['timeout_seconds']} ledger={ledger}"
                            )
                        elif incident["event"] == "restart_interrupted_turn_replayed":
                            line = (
                                "[hermes-live-watchdog] RESTART_INTERRUPTED_TURN_REPLAYED "
                                f"ordinal={incident['user_ordinal']} ledger={ledger}"
                            )
                        elif incident["event"] == "tool_iteration_exhausted":
                            line = (
                                "[hermes-live-watchdog] TOOL_ITERATION_EXHAUSTED "
                                f"used={incident['iteration_count']} "
                                f"limit={incident['iteration_limit']} ledger={ledger}"
                            )
                        elif incident["event"] == "tool_failure":
                            line = (
                                "[hermes-live-watchdog] TOOL_FAILURE "
                                f"tool={incident['tool']} "
                                f"category={incident['error_category']} ledger={ledger}"
                            )
                        elif incident["event"] == "tool_recovered":
                            line = (
                                "[hermes-live-watchdog] TOOL_RECOVERED "
                                f"tool={incident['tool']} ledger={ledger}"
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
                            elif incident["event"] == "tool_failure":
                                title = "Hermes tool failure detected"
                            elif incident["event"] == "tool_recovered":
                                title = "Hermes tool retry recovered"
                            else:
                                title = "Hermes session recovery failed"
                            desktop_notify(title, line)
                if not sid:
                    continue
                state_key = turn_state_key(ledger, row)
                if is_terminal(row):
                    terminal_keys = [state_key]
                    if not row.get("turn_id"):
                        terminal_keys = [
                            key
                            for key in states
                            if key[0] == str(ledger) and key[1] == sid
                        ] or terminal_keys
                    for terminal_key in terminal_keys:
                        states.pop(terminal_key, None)
                        state_ledgers.pop(terminal_key, None)
                    continue
                if not is_progress(row):
                    continue
                state = states.get(state_key)
                if state is None:
                    state = TurnState(session_id=sid)
                    states[state_key] = state
                state_ledgers[state_key] = ledger
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
            if now - last_incident < monitor_stale_alert_cooldown(args, source):
                continue
            incident_alerted_at[incident_key] = now
            alert = build_monitor_stale_alert(
                source=source,
                age=age,
                threshold=threshold,
                ledger=ledger,
                heartbeat_seen=(ledger, source) in monitor_heartbeat_seen,
            )
            append_alert_jsonl(alerts, alert)
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

        for state_key, state in list(states.items()):
            ledger = state_ledgers.get(state_key) or Path("")
            if state.producer_pid and not producer_identity_alive(
                state.producer_pid, state.producer_start_ticks
            ):
                alert = build_orphan_alert(state, ledger)
                append_alert_jsonl(alerts, alert)
                _record_watchdog_incident(
                    home,
                    profile_for_monitor_ledger(home, ledger, args.monitor_profile),
                    alert,
                    state=state,
                )
                latest.write_text(
                    json.dumps(alert, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
                print(
                    f"[hermes-live-watchdog] ORPHAN sid={state.session_id[:8]} "
                    f"key={state.session_key} last={state.last_event} ledger={ledger}",
                    flush=True,
                )
                states.pop(state_key, None)
                state_ledgers.pop(state_key, None)
                continue
            if state.waiting:
                continue
            last = state.last_progress_at or state.started_at
            idle = now - last
            elapsed = now - (state.started_at or last)
            if idle < args.idle_seconds:
                continue
            if now - state.last_alert_at < args.alert_cooldown:
                continue
            state.last_alert_at = now
            alert = build_alert(state, idle, elapsed, args.idle_seconds, ledger)
            append_alert_jsonl(alerts, alert)
            _record_watchdog_incident(
                home,
                profile_for_monitor_ledger(home, ledger, args.monitor_profile),
                alert,
                state=state,
            )
            latest.write_text(
                json.dumps(alert, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            line = (
                f"[hermes-live-watchdog] STUCK sid={state.session_id[:8]} "
                f"key={state.session_key} idle={idle:.1f}s last={state.last_event} ledger={ledger}"
            )
            print(line, flush=True)
            if args.notify:
                desktop_notify("Hermes turn may be stuck", line)

        _ingest_improvement_supervisor_events(home)
        _tick_improvement_supervisor_repair(home)
        write_heartbeat()
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
    parser.add_argument(
        "--notify",
        action="store_false",
        dest="notify",
        default=False,
        help="Deprecated compatibility flag; watchdog incidents are internal-only",
    )
    parser.add_argument("--once", action="store_true", help="Process available rows once and exit")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(run(parse_args(sys.argv[1:])))
