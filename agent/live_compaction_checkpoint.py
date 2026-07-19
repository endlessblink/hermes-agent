"""Off-path, version-fenced context compaction checkpoints.

The foreground compaction path may consume a prepared checkpoint only when the
durable message prefix is semantically identical to the snapshot summarized by
the worker. Persistence-only metadata may change across a database reload;
messages appended while preparation runs are retained verbatim.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows
    _fcntl = None  # type: ignore[assignment]

try:
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - POSIX
    _msvcrt = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 2
_flights_lock = threading.Lock()
_flights: set[str] = set()
_worker_slots = threading.BoundedSemaphore(1)
_path_locks_guard = threading.Lock()
_path_locks: dict[str, threading.RLock] = {}

# Exact top-level fields that affect the compressor's boundary selection or
# summary input. Session reload also restores timestamps, finish reasons,
# reasoning carriers, platform ids, and FTS helper names; those fields are not
# read by ContextCompressor and must not invalidate an otherwise identical
# checkpoint after restart.
_COMPACTION_SEMANTIC_FIELDS = {
    "role",
    "content",
    "tool_call_id",
    "tool_calls",
    "_compressed_summary",
}


def _messages_digest(messages: list[dict[str, Any]]) -> str:
    normalized = []
    for message in messages:
        if isinstance(message, dict):
            message = {
                key: value
                for key, value in message.items()
                if key in _COMPACTION_SEMANTIC_FIELDS
            }
        normalized.append(message)
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class LiveCompactionCheckpointStore:
    """Persist prepared checkpoints and atomically validate them at apply time."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _path(self, session_id: str) -> Path:
        safe_id = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        return self.root / f"{safe_id}.json"

    def _lock_path(self, session_id: str) -> Path:
        return self._path(session_id).with_suffix(".lock")

    @contextmanager
    def _session_lock(self, session_id: str):
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self._lock_path(session_id)
        lock_key = str(lock_path.resolve())
        with _path_locks_guard:
            local_lock = _path_locks.setdefault(lock_key, threading.RLock())
        with local_lock:
            with lock_path.open("a+b") as handle:
                if _fcntl is not None:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
                elif _msvcrt is not None:  # pragma: no cover - Windows
                    if handle.seek(0, os.SEEK_END) == 0:
                        handle.write(b"\0")
                        handle.flush()
                    handle.seek(0)
                    _msvcrt.locking(handle.fileno(), _msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    if _fcntl is not None:
                        _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
                    elif _msvcrt is not None:  # pragma: no cover - Windows
                        handle.seek(0)
                        _msvcrt.locking(handle.fileno(), _msvcrt.LK_UNLCK, 1)

    def _read_record_unlocked(self, session_id: str) -> Optional[dict[str, Any]]:
        try:
            record = json.loads(self._path(session_id).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        return record if isinstance(record, dict) else None

    def peek(self, session_id: str) -> Optional[Path]:
        path = self._path(session_id)
        return path if path.is_file() else None

    def publish(
        self,
        session_id: str,
        snapshot: list[dict[str, Any]],
        prepared: list[dict[str, Any]],
        *,
        strategy_fingerprint: str = "",
        snapshot_tokens: int = 0,
        expected_record_id: Optional[str] = None,
    ) -> bool:
        self.root.mkdir(parents=True, exist_ok=True)
        record = {
            "schema_version": _SCHEMA_VERSION,
            "record_id": uuid.uuid4().hex,
            "session_id": session_id,
            "snapshot_length": len(snapshot),
            "snapshot_digest": _messages_digest(snapshot),
            "strategy_fingerprint": strategy_fingerprint,
            "snapshot_tokens": max(0, int(snapshot_tokens or 0)),
            "prepared_messages": prepared,
            "created_at": time.time(),
        }
        fd, raw_tmp = tempfile.mkstemp(prefix=".checkpoint-", dir=self.root)
        tmp_path = Path(raw_tmp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_path, 0o600)
            with self._session_lock(session_id):
                existing = self._read_record_unlocked(session_id)
                if expected_record_id is not None and (
                    existing is None
                    or existing.get("record_id") != expected_record_id
                ):
                    return False
                if existing is not None:
                    existing_length = existing.get("snapshot_length")
                    existing_digest = existing.get("snapshot_digest")
                    if isinstance(existing_length, int) and (
                        existing_length > len(snapshot)
                        or (
                            existing_length == len(snapshot)
                            and existing_digest != record["snapshot_digest"]
                        )
                    ):
                        return False
                os.replace(tmp_path, self._path(session_id))
                if hasattr(os, "O_DIRECTORY"):
                    directory_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
            return True
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def is_current(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        *,
        strategy_fingerprint: str = "",
    ) -> bool:
        """Return whether the stored snapshot is an unchanged message prefix."""
        path = self._path(session_id)
        with self._session_lock(session_id):
            record = self._read_record_unlocked(session_id)
            if record is None:
                return False
            snapshot_length = record.get("snapshot_length")
            valid_shape = (
                record.get("schema_version") == _SCHEMA_VERSION
                and record.get("session_id") == session_id
                and record.get("strategy_fingerprint") == strategy_fingerprint
                and isinstance(snapshot_length, int)
                and snapshot_length >= 0
                and isinstance(record.get("prepared_messages"), list)
                and len(messages) >= snapshot_length
            )
            is_current = bool(
                valid_shape
                and _messages_digest(messages[:snapshot_length])
                == record.get("snapshot_digest")
            )
            if not is_current:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            return is_current

    def current_coverage(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        *,
        strategy_fingerprint: str = "",
    ) -> Optional[dict[str, Any]]:
        """Return current checkpoint coverage without consuming it."""
        path = self._path(session_id)
        with self._session_lock(session_id):
            record = self._read_record_unlocked(session_id)
            if record is None:
                return None
            snapshot_length = record.get("snapshot_length")
            snapshot_tokens = record.get("snapshot_tokens", 0)
            valid = bool(
                record.get("schema_version") == _SCHEMA_VERSION
                and record.get("session_id") == session_id
                and record.get("strategy_fingerprint") == strategy_fingerprint
                and isinstance(snapshot_length, int)
                and snapshot_length >= 0
                and isinstance(snapshot_tokens, int)
                and snapshot_tokens >= 0
                and isinstance(record.get("prepared_messages"), list)
                and len(messages) >= snapshot_length
                and _messages_digest(messages[:snapshot_length])
                == record.get("snapshot_digest")
            )
            if not valid:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                return None
            return {
                "snapshot_length": snapshot_length,
                "snapshot_tokens": snapshot_tokens,
                "record_id": str(record.get("record_id") or ""),
            }

    def consume_if_current(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        *,
        strategy_fingerprint: str = "",
    ) -> Optional[list[dict[str, Any]]]:
        path = self._path(session_id)
        with self._session_lock(session_id):
            record = self._read_record_unlocked(session_id)
            if record is None:
                return None
            snapshot_length = record.get("snapshot_length")
            prepared = record.get("prepared_messages")
            valid_shape = (
                record.get("schema_version") == _SCHEMA_VERSION
                and record.get("session_id") == session_id
                and record.get("strategy_fingerprint") == strategy_fingerprint
                and isinstance(snapshot_length, int)
                and snapshot_length >= 0
                and isinstance(prepared, list)
                and len(messages) >= snapshot_length
            )
            prefix = messages[:snapshot_length] if valid_shape else []
            is_current = bool(
                valid_shape
                and _messages_digest(prefix) == record.get("snapshot_digest")
            )
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            if not is_current:
                return None
            assert isinstance(prepared, list)
            return copy.deepcopy(prepared) + copy.deepcopy(messages[snapshot_length:])


def schedule_live_compaction_checkpoint(
    *,
    store: LiveCompactionCheckpointStore,
    session_id: str,
    messages: list[dict[str, Any]],
    prepare: Callable[[list[dict[str, Any]]], Optional[list[dict[str, Any]]]],
    strategy_fingerprint: str = "",
    replace_current: bool = False,
    snapshot_tokens: int = 0,
    expected_record_id: Optional[str] = None,
) -> bool:
    """Start one daemon preparation job per store/session without blocking."""

    if not session_id or not messages:
        return False
    if store.peek(session_id) is not None:
        if replace_current and expected_record_id is None:
            coverage = store.current_coverage(
                session_id,
                messages,
                strategy_fingerprint=strategy_fingerprint,
            )
            if coverage is None:
                return False
            expected_record_id = coverage["record_id"]
        elif not replace_current and store.is_current(
            session_id,
            messages,
            strategy_fingerprint=strategy_fingerprint,
        ):
            return False
    flight_key = f"{store.root.resolve()}::{session_id}"
    if not _worker_slots.acquire(blocking=False):
        return False
    with _flights_lock:
        if flight_key in _flights:
            _worker_slots.release()
            return False
        _flights.add(flight_key)

    snapshot = copy.deepcopy(messages)

    def _run() -> None:
        started_at = time.monotonic()
        try:
            prepared = prepare(snapshot)
            if prepared and prepared != snapshot:
                published = store.publish(
                    session_id,
                    snapshot,
                    prepared,
                    strategy_fingerprint=strategy_fingerprint,
                    snapshot_tokens=snapshot_tokens,
                    expected_record_id=expected_record_id,
                )
                logger.info(
                    "background context checkpoint %s: session=%s messages=%d->%d "
                    "duration_ms=%d",
                    "completed" if published else "superseded",
                    session_id,
                    len(snapshot),
                    len(prepared),
                    int((time.monotonic() - started_at) * 1000),
                )
            else:
                logger.warning(
                    "background context checkpoint produced no healthy result: "
                    "session=%s duration_ms=%d",
                    session_id,
                    int((time.monotonic() - started_at) * 1000),
                )
        except Exception:
            logger.warning(
                "background context checkpoint preparation failed for session=%s",
                session_id,
                exc_info=True,
            )
        finally:
            with _flights_lock:
                _flights.discard(flight_key)
            _worker_slots.release()

    threading.Thread(
        target=_run,
        name=f"live-compaction-{hashlib.sha256(session_id.encode()).hexdigest()[:8]}",
        daemon=True,
    ).start()
    return True


__all__ = [
    "LiveCompactionCheckpointStore",
    "schedule_live_compaction_checkpoint",
]
