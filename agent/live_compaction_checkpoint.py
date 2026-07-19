"""Off-path, version-fenced context compaction checkpoints.

The foreground compaction path may consume a prepared checkpoint only when the
durable message prefix is byte-for-byte identical to the snapshot summarized by
the worker. Messages appended while preparation runs are retained verbatim.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional


logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1
_flights_lock = threading.Lock()
_flights: set[str] = set()


def _messages_digest(messages: list[dict[str, Any]]) -> str:
    normalized = []
    for message in messages:
        if isinstance(message, dict):
            message = {
                key: value
                for key, value in message.items()
                if key != "_db_persisted"
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

    def peek(self, session_id: str) -> Optional[Path]:
        path = self._path(session_id)
        return path if path.is_file() else None

    def publish(
        self,
        session_id: str,
        snapshot: list[dict[str, Any]],
        prepared: list[dict[str, Any]],
    ) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        record = {
            "schema_version": _SCHEMA_VERSION,
            "session_id": session_id,
            "snapshot_length": len(snapshot),
            "snapshot_digest": _messages_digest(snapshot),
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
            os.replace(tmp_path, self._path(session_id))
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def is_current(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
    ) -> bool:
        """Return whether the stored snapshot is an unchanged message prefix."""
        path = self._path(session_id)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return False
        snapshot_length = record.get("snapshot_length")
        valid_shape = (
            record.get("schema_version") == _SCHEMA_VERSION
            and record.get("session_id") == session_id
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

    def consume_if_current(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
    ) -> Optional[list[dict[str, Any]]]:
        path = self._path(session_id)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None

        snapshot_length = record.get("snapshot_length")
        prepared = record.get("prepared_messages")
        valid_shape = (
            record.get("schema_version") == _SCHEMA_VERSION
            and record.get("session_id") == session_id
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
        return copy.deepcopy(prepared) + copy.deepcopy(messages[snapshot_length:])


def schedule_live_compaction_checkpoint(
    *,
    store: LiveCompactionCheckpointStore,
    session_id: str,
    messages: list[dict[str, Any]],
    prepare: Callable[[list[dict[str, Any]]], Optional[list[dict[str, Any]]]],
) -> bool:
    """Start one daemon preparation job per store/session without blocking."""

    if not session_id or not messages:
        return False
    if store.peek(session_id) is not None and store.is_current(session_id, messages):
        return False
    flight_key = f"{store.root.resolve()}::{session_id}"
    with _flights_lock:
        if flight_key in _flights:
            return False
        _flights.add(flight_key)

    snapshot = copy.deepcopy(messages)

    def _run() -> None:
        try:
            prepared = prepare(snapshot)
            if prepared and prepared != snapshot:
                store.publish(session_id, snapshot, prepared)
        except Exception:
            logger.warning(
                "background context checkpoint preparation failed for session=%s",
                session_id,
                exc_info=True,
            )
        finally:
            with _flights_lock:
                _flights.discard(flight_key)

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
