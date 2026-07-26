"""Profile-scoped proposal store for the improvement supervisor."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Any, Iterator
import unicodedata

from hermes_constants import get_default_hermes_root, get_hermes_home
from utils import atomic_json_write

from .privacy import redact_for_review


SCHEMA_VERSION = 1
VALID_STATUSES = frozenset({"pending", "accepted", "dismissed"})
VALID_AUTHORITIES = frozenset({"proposal_only", "runtime_repaired"})
REPAIR_LIFECYCLE_STATUSES = frozenset(
    {"queued", "running", "verifying", "candidate_ready", "failed", "timed_out", "cancelled"}
)
_REPAIR_LIFECYCLE_TRANSITIONS = {
    "queued": frozenset({"running", "failed", "timed_out", "cancelled"}),
    "running": frozenset({"verifying", "failed", "timed_out", "cancelled"}),
    "verifying": frozenset({"candidate_ready", "failed", "timed_out", "cancelled"}),
    "candidate_ready": frozenset(),
    "failed": frozenset(),
    "timed_out": frozenset(),
    "cancelled": frozenset(),
}
_REPAIR_EXECUTION_TRANSITIONS = {
    "admitted": frozenset({"launching", "rejected"}),
    "launching": frozenset({"running", "gave_up", "cleanup_failed"}),
    "running": frozenset({"sealing", "gave_up", "cleanup_failed"}),
    "sealing": frozenset({"verifying", "gave_up", "cleanup_failed"}),
    "verifying": frozenset({"candidate_ready", "gave_up", "cleanup_failed"}),
    "cleanup_failed": frozenset({"rejected", "gave_up"}),
    "candidate_ready": frozenset(),
    "rejected": frozenset(),
    "gave_up": frozenset(),
}
_lock = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def state_dir() -> Path:
    return get_hermes_home() / "state" / "improvement-supervisor"


def proposals_path() -> Path:
    return state_dir() / "proposals.json"


def audit_path() -> Path:
    return state_dir() / "audit.jsonl"


def repair_admission_path() -> Path:
    return (
        get_default_hermes_root()
        / "state"
        / "improvement-supervisor-global"
        / "repair-admission.json"
    )


def repair_lifecycle_path() -> Path:
    return repair_admission_path().with_name("repair-lifecycle.json")


def repair_execution_path() -> Path:
    return repair_admission_path().with_name("repair-execution.json")


@contextmanager
def _file_process_lock(root: Path, filename: str) -> Iterator[None]:
    with _lock:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(root, 0o700)
        except OSError:
            pass
        lock_path = root / filename
        with lock_path.open("a+b") as handle:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                if handle.read(1) == b"":
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _process_lock() -> Iterator[None]:
    """Serialize proposal writes across threads and local Hermes processes."""
    with _file_process_lock(state_dir(), ".proposals.lock"):
        yield


@contextmanager
def _repair_process_lock() -> Iterator[None]:
    """Serialize the single repair slot across profiles and local processes."""
    with _file_process_lock(repair_admission_path().parent, ".repair-admission.lock"):
        yield


@contextmanager
def runtime_event_ingest_lock() -> Iterator[None]:
    """Serialize one profile's runtime event ingestion across processes."""
    with _file_process_lock(state_dir(), ".runtime-events-ingest.lock"):
        yield


def _load_unlocked() -> dict[str, Any]:
    path = proposals_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"version": SCHEMA_VERSION, "proposals": []}
    if not isinstance(value, dict) or not isinstance(value.get("proposals"), list):
        return {"version": SCHEMA_VERSION, "proposals": []}
    return value


def _save_unlocked(data: dict[str, Any]) -> None:
    data["version"] = SCHEMA_VERSION
    data["updated_at"] = _now()
    atomic_json_write(proposals_path(), data, indent=2, mode=0o600, sort_keys=True)


def _clean_text(value: Any, limit: int) -> str:
    return redact_for_review(value, limit)


def _append_audit_unlocked(event: str, proposal_id: str, status: str) -> None:
    payload = {
        "ts": _now(),
        "event": event,
        "proposal_id": proposal_id,
        "status": status,
    }
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    fd = os.open(audit_path(), flags, 0o600)
    try:
        os.write(fd, (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.chmod(audit_path(), 0o600)
    except OSError:
        pass


def _normalized_key(value: Any) -> str:
    clean = _clean_text(value, 160)
    normalized = unicodedata.normalize("NFKC", clean).casefold()
    key = re.sub(r"[^\w]+", "-", normalized, flags=re.UNICODE).strip("-_")
    if not key:
        key = "unicode-" + hashlib.sha256(clean.encode("utf-8")).hexdigest()[:24]
    return key[:160]


def _resolve(proposals: list[dict[str, Any]], ref: str) -> dict[str, Any] | None:
    clean = str(ref or "").strip()
    if clean.isdigit():
        pending = [item for item in proposals if item.get("status") == "pending"]
        index = int(clean) - 1
        if 0 <= index < len(pending):
            return pending[index]
    matches = [item for item in proposals if str(item.get("id", "")).startswith(clean)]
    return matches[0] if len(matches) == 1 else None


def list_proposals(status: str | None = None) -> list[dict[str, Any]]:
    with _process_lock():
        proposals = list(_load_unlocked()["proposals"])
    if status is not None:
        proposals = [item for item in proposals if item.get("status") == status]
    return proposals


def get_proposal(ref: str) -> dict[str, Any] | None:
    with _process_lock():
        return _resolve(_load_unlocked()["proposals"], ref)


def _load_repair_admission_unlocked() -> dict[str, Any]:
    try:
        value = json.loads(repair_admission_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def get_repair_admission() -> dict[str, Any]:
    with _repair_process_lock():
        return dict(_load_repair_admission_unlocked())


def _load_repair_lifecycle_unlocked() -> dict[str, Any]:
    try:
        path = repair_lifecycle_path()
        if path.stat().st_size > 64 * 1024:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(value, dict):
        return {}
    task_id = str(value.get("taskId") or "")[:80]
    status = str(value.get("status") or "")
    if not task_id or status not in REPAIR_LIFECYCLE_STATUSES:
        return {}
    return {
        "schemaVersion": 1,
        "taskId": task_id,
        "status": status,
        "updatedAt": str(value.get("updatedAt") or "")[:40],
        "outcomeCode": str(value.get("outcomeCode") or "")[:80],
    }


def get_repair_lifecycle() -> dict[str, Any]:
    with _repair_process_lock():
        return dict(_load_repair_lifecycle_unlocked())


def transition_repair_lifecycle(
    task_id: str, status: str, *, outcome_code: str = ""
) -> bool:
    """Persist one monotonic, bounded public repair lifecycle transition."""
    clean_task_id = str(task_id or "")[:80]
    clean_status = str(status or "")
    clean_outcome = str(outcome_code or "")[:80]
    if not clean_task_id or clean_status not in REPAIR_LIFECYCLE_STATUSES:
        return False
    if clean_outcome and not re.fullmatch(r"[a-z0-9_]{1,80}", clean_outcome):
        return False
    with _repair_process_lock():
        current = _load_repair_lifecycle_unlocked()
        if current:
            if current.get("taskId") != clean_task_id:
                if current.get("status") not in {
                    "candidate_ready",
                    "failed",
                    "timed_out",
                    "cancelled",
                }:
                    return False
            elif clean_status != current.get("status") and clean_status not in _REPAIR_LIFECYCLE_TRANSITIONS.get(
                str(current.get("status") or ""), frozenset()
            ):
                return False
        elif clean_status != "queued":
            return False
        payload = {
            "schemaVersion": 1,
            "taskId": clean_task_id,
            "status": clean_status,
            "updatedAt": _now(),
            "outcomeCode": clean_outcome,
        }
        atomic_json_write(
            repair_lifecycle_path(), payload, indent=2, mode=0o600, sort_keys=True
        )
        return True


def _load_repair_execution_unlocked() -> dict[str, Any]:
    try:
        path = repair_execution_path()
        if path.stat().st_size > 64 * 1024:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def get_repair_execution() -> dict[str, Any]:
    with _repair_process_lock():
        return dict(_load_repair_execution_unlocked())


def initialize_repair_execution(
    task_id: str,
    *,
    fingerprint: str,
    source_digest: str,
    snapshot_path: str,
) -> bool:
    clean_task_id = str(task_id or "")[:80]
    if (
        not clean_task_id
        or not re.fullmatch(r"[0-9a-f]{64}", str(fingerprint or ""))
        or not re.fullmatch(r"[0-9a-f]{64}", str(source_digest or ""))
        or not Path(snapshot_path).is_absolute()
    ):
        return False
    with _repair_process_lock():
        current = _load_repair_execution_unlocked()
        if current and current.get("state") not in {"candidate_ready", "rejected", "gave_up"}:
            return False
        payload = {
            "schema_version": 1,
            "task_id": clean_task_id,
            "fingerprint": fingerprint,
            "source_digest": source_digest,
            "snapshot_path": str(snapshot_path)[:1000],
            "state": "admitted",
            "reason_code": "incident_admitted",
            "attempt": 0,
            "run_id": None,
            "unit_name": "",
            "updated_at": _now(),
        }
        atomic_json_write(
            repair_execution_path(), payload, indent=2, mode=0o600, sort_keys=True
        )
        return True


def transition_repair_execution(
    task_id: str,
    state: str,
    *,
    reason_code: str = "",
    run_id: int | None = None,
    unit_name: str = "",
    **fields: Any,
) -> bool:
    with _repair_process_lock():
        current = _load_repair_execution_unlocked()
        current_state = str(current.get("state") or "")
        if current.get("task_id") != str(task_id or ""):
            return False
        if state != current_state and state not in _REPAIR_EXECUTION_TRANSITIONS.get(
            current_state, frozenset()
        ):
            return False
        if reason_code and not re.fullmatch(r"[a-z0-9_]{1,80}", reason_code):
            return False
        current["state"] = state
        current["updated_at"] = _now()
        if reason_code:
            current["reason_code"] = reason_code
        if run_id is not None:
            current["run_id"] = max(0, int(run_id))
        if unit_name:
            if not re.fullmatch(r"hermes-repair-[a-zA-Z0-9_.-]+\.service", unit_name):
                return False
            current["unit_name"] = unit_name
        for key in ("output_dir", "manifest_path", "patch_path", "deadline_at"):
            if key in fields and fields[key] is not None:
                current[key] = str(fields[key])[:1000]
        if state == "launching":
            current["attempt"] = 1
        atomic_json_write(
            repair_execution_path(), current, indent=2, mode=0o600, sort_keys=True
        )
        return True


def try_claim_repair_admission(
    fingerprint: str, *, stale_after_seconds: float = 300.0
) -> dict[str, Any]:
    """Claim the one global repair feeder slot.

    A task-backed claim is never expired here: its caller must verify the task
    reached a terminal state and clear it explicitly. Only a process that died
    between claiming the slot and creating the task can be reclaimed by age.
    """

    now = time.time()
    with _repair_process_lock():
        current = _load_repair_admission_unlocked()
        if current:
            claimed_at = float(current.get("claimed_at") or 0.0)
            stale_claim = (
                current.get("status") == "claimed"
                and claimed_at > 0
                and now - claimed_at >= max(1.0, float(stale_after_seconds))
            )
            if not stale_claim:
                return {"claimed": False, "admission": dict(current)}
        token = secrets.token_hex(16)
        admission = {
            "version": 1,
            "status": "claimed",
            "token": token,
            "fingerprint": str(fingerprint or "")[:64],
            "claimed_at": now,
            "task_id": "",
        }
        atomic_json_write(
            repair_admission_path(), admission, indent=2, mode=0o600, sort_keys=True
        )
        return {"claimed": True, "admission": admission}


def commit_repair_admission(token: str, task_id: str) -> bool:
    with _repair_process_lock():
        current = _load_repair_admission_unlocked()
        if current.get("status") != "claimed" or current.get("token") != token:
            return False
        current["status"] = "task_created"
        current["task_id"] = str(task_id or "")[:80]
        current["committed_at"] = time.time()
        atomic_json_write(
            repair_admission_path(), current, indent=2, mode=0o600, sort_keys=True
        )
        return True


def release_repair_admission(token: str) -> bool:
    with _repair_process_lock():
        current = _load_repair_admission_unlocked()
        if current.get("status") != "claimed" or current.get("token") != token:
            return False
        try:
            repair_admission_path().unlink()
        except FileNotFoundError:
            pass
        return True


def clear_repair_admission(task_id: str) -> bool:
    with _repair_process_lock():
        current = _load_repair_admission_unlocked()
        if current.get("task_id") != str(task_id or ""):
            return False
        try:
            repair_admission_path().unlink()
        except FileNotFoundError:
            pass
        return True


def record_proposal(review: dict[str, Any]) -> dict[str, Any]:
    category = _clean_text(review.get("category"), 40)
    dedup_key = _normalized_key(review.get("dedup_key"))
    issue_key = hashlib.sha256(f"{category}\0{dedup_key}".encode("utf-8")).hexdigest()
    authority = _clean_text(review.get("authority"), 40)
    if authority not in VALID_AUTHORITIES:
        authority = "proposal_only"
    now = _now()
    with _process_lock():
        data = _load_unlocked()
        for item in data["proposals"]:
            if item.get("issue_key") != issue_key:
                continue
            item["occurrences"] = int(item.get("occurrences") or 0) + 1
            if authority == "runtime_repaired":
                item["containment_occurrences"] = int(
                    item.get("containment_occurrences") or 0
                ) + 1
            item["last_seen_at"] = now
            item["confidence"] = _clean_text(review.get("confidence"), 12)
            item["evidence"] = _clean_text(review.get("evidence"), 600)
            item["next_check"] = _clean_text(review.get("next_check"), 500)
            if authority == "runtime_repaired":
                item["authority"] = authority
                item["containment_status"] = "applied"
            _save_unlocked(data)
            _append_audit_unlocked("proposal_seen", item["id"], item["status"])
            return dict(item)

        record = {
            "id": issue_key[:12],
            "issue_key": issue_key,
            "dedup_key": dedup_key,
            "category": category,
            "title": _clean_text(review.get("title"), 160),
            "summary": _clean_text(review.get("summary"), 800),
            "confidence": _clean_text(review.get("confidence"), 12),
            "evidence": _clean_text(review.get("evidence"), 600),
            "next_check": _clean_text(review.get("next_check"), 500),
            "status": "pending",
            "authority": authority,
            "containment_status": (
                "applied" if authority == "runtime_repaired" else "not_applied"
            ),
            "occurrences": 1,
            "containment_occurrences": (
                1 if authority == "runtime_repaired" else 0
            ),
            "created_at": now,
            "last_seen_at": now,
        }
        data["proposals"].append(record)
        _save_unlocked(data)
        _append_audit_unlocked("proposal_created", record["id"], record["status"])
        return dict(record)


def _set_status(ref: str, status: str) -> bool:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid proposal status: {status}")
    with _process_lock():
        data = _load_unlocked()
        item = _resolve(data["proposals"], ref)
        if item is None:
            return False
        item["status"] = status
        item["resolved_at"] = _now()
        _save_unlocked(data)
        _append_audit_unlocked("status_changed", item["id"], status)
        return True


def accept_proposal(ref: str) -> bool:
    return _set_status(ref, "accepted")


def dismiss_proposal(ref: str) -> bool:
    return _set_status(ref, "dismissed")
