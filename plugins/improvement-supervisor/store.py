"""Profile-scoped proposal store for the improvement supervisor."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Iterator
import unicodedata

from hermes_constants import get_hermes_home
from utils import atomic_json_write

from .privacy import redact_for_review


SCHEMA_VERSION = 1
VALID_STATUSES = frozenset({"pending", "accepted", "dismissed"})
VALID_AUTHORITIES = frozenset({"proposal_only", "runtime_repaired"})
_lock = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def state_dir() -> Path:
    return get_hermes_home() / "state" / "improvement-supervisor"


def proposals_path() -> Path:
    return state_dir() / "proposals.json"


def audit_path() -> Path:
    return state_dir() / "audit.jsonl"


@contextmanager
def _process_lock() -> Iterator[None]:
    """Serialize load-modify-save across threads and local Hermes processes."""
    with _lock:
        root = state_dir()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(root, 0o700)
        except OSError:
            pass
        lock_path = root / ".proposals.lock"
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
