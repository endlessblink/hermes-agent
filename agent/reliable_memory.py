"""Versioned memory ledger with a verified, editable Obsidian mirror.

The SQLite ledger keeps history. A revision is only active after its managed
Markdown note and manifest have been atomically written and read back. Every
read reconciles the mirror first, so stale or unsafe notes fail closed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from utils import atomic_replace

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback is process-local
    fcntl = None


SCHEMA_VERSION = 1
DEFAULT_MIRROR_FOLDER = "_System/Hermes Knowledge Graph/Memory"
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_TOKEN = re.compile(r"[\w\u0590-\u05ff]+", re.UNICODE)


class MemoryMirrorError(RuntimeError):
    """Raised when a ledger event cannot be verified in Obsidian."""


class MemoryConflictError(RuntimeError):
    """Raised when a requested memory revision is stale or ambiguous."""


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ReliableMemoryRepository:
    """Profile-local append-only memory history and managed Markdown mirror."""

    def __init__(
        self,
        *,
        db_path: Path | str,
        mirror_root: Path | str,
        threat_scan: Callable[[str], list[str]] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.mirror_root = Path(mirror_root)
        self.manifest_path = self.mirror_root / "manifest.json"
        self._thread_lock = threading.RLock()
        self._threat_scan = threat_scan or self._default_threat_scan
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.mirror_root.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @classmethod
    def from_profile(cls, config: dict[str, Any] | None = None) -> "ReliableMemoryRepository":
        from agent.vault_knowledge.config import load_vault_config
        from agent.vault_knowledge.path_policy import VaultBoundary
        from hermes_constants import get_hermes_home

        if config is None:
            try:
                from hermes_cli.config import load_config_readonly

                config = load_config_readonly()
            except Exception:
                config = {}
        memory_config = config.get("memory", {}) if isinstance(config, dict) else {}
        if not isinstance(memory_config, dict):
            memory_config = {}
        vault_config = load_vault_config(config)
        boundary = VaultBoundary(vault_config)
        relative_folder = str(
            memory_config.get("obsidian_memory_folder") or DEFAULT_MIRROR_FOLDER
        ).strip()
        boundary.validate_write_target(f"{relative_folder}/manifest.json")
        return cls(
            db_path=get_hermes_home() / "memory.db",
            mirror_root=boundary.visible_workspace / relative_folder,
        )

    @staticmethod
    def _default_threat_scan(content: str) -> list[str]:
        try:
            from tools.threat_patterns import scan_for_threats

            return list(scan_for_threats(content, scope="strict"))
        except Exception:
            return []

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_events (
                    event_id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    content TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    scope_json TEXT NOT NULL,
                    trust TEXT NOT NULL,
                    source_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    note_path TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    supersedes_revision INTEGER,
                    UNIQUE(memory_id, revision)
                );

                CREATE INDEX IF NOT EXISTS memory_events_status_idx
                    ON memory_events(status);
                CREATE INDEX IF NOT EXISTS memory_events_memory_idx
                    ON memory_events(memory_id, revision DESC);

                CREATE TABLE IF NOT EXISTS current_memories (
                    memory_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    revision INTEGER NOT NULL,
                    FOREIGN KEY(event_id) REFERENCES memory_events(event_id)
                );

                CREATE TABLE IF NOT EXISTS memory_sync_issues (
                    memory_id TEXT PRIMARY KEY,
                    reason TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    observed_at REAL NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO memory_meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        with self._thread_lock:
            lock_path = self.mirror_root / ".sync.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = lock_path.open("a+", encoding="utf-8")
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()

    def note_path(self, memory_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9-]{36}", memory_id):
            raise ValueError("Invalid memory id")
        return self.mirror_root / f"{memory_id}.md"

    def add(
        self,
        content: str,
        *,
        memory_type: str,
        trust: str,
        scope: dict[str, Any] | None = None,
        source: dict[str, Any] | None = None,
        memory_id: str | None = None,
    ) -> dict[str, Any]:
        content = self._validate_content(content)
        memory_id = memory_id or str(uuid.uuid4())
        with self._exclusive():
            if self._current_row(memory_id) is not None:
                raise MemoryConflictError(f"Memory already exists: {memory_id}")
            return self._commit_revision_locked(
                memory_id=memory_id,
                revision=1,
                operation="add",
                content=content,
                memory_type=memory_type,
                trust=trust,
                scope=scope or {"kind": "global"},
                source=source or {},
                supersedes_revision=None,
            )

    def correct(
        self,
        memory_id: str,
        content: str,
        *,
        trust: str,
        source: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        content = self._validate_content(content)
        with self._exclusive():
            current = self._require_current(memory_id)
            return self._commit_revision_locked(
                memory_id=memory_id,
                revision=int(current["revision"]) + 1,
                operation="correct",
                content=content,
                memory_type=current["memory_type"],
                trust=trust,
                scope=json.loads(current["scope_json"]),
                source=source or {},
                supersedes_revision=int(current["revision"]),
            )

    def forget(
        self,
        memory_id: str,
        *,
        source: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._exclusive():
            current = self._require_current(memory_id)
            result = self._commit_revision_locked(
                memory_id=memory_id,
                revision=int(current["revision"]) + 1,
                operation="forget",
                content=current["content"],
                memory_type=current["memory_type"],
                trust=current["trust"],
                scope=json.loads(current["scope_json"]),
                source=source or {},
                supersedes_revision=int(current["revision"]),
                active=False,
            )
            return result

    def undo(self, memory_id: str) -> dict[str, Any]:
        with self._exclusive():
            current = self._latest_row(memory_id)
            if current is None:
                raise MemoryConflictError(f"Unknown memory: {memory_id}")
            with self._connect() as connection:
                previous = connection.execute(
                    """
                    SELECT * FROM memory_events
                    WHERE memory_id = ? AND revision < ? AND operation != 'forget'
                    ORDER BY revision DESC LIMIT 1
                    """,
                    (memory_id, current["revision"]),
                ).fetchone()
            if previous is None:
                raise MemoryConflictError("No previous revision to restore")
            return self._commit_revision_locked(
                memory_id=memory_id,
                revision=int(current["revision"]) + 1,
                operation="undo",
                content=previous["content"],
                memory_type=previous["memory_type"],
                trust=previous["trust"],
                scope=json.loads(previous["scope_json"]),
                source={"kind": "undo", "revision": int(previous["revision"])},
                supersedes_revision=int(current["revision"]),
            )

    def purge(self, memory_id: str) -> dict[str, Any]:
        with self._exclusive():
            with self._connect() as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM memory_events WHERE memory_id = ?",
                    (memory_id,),
                ).fetchone()[0]
                if not count:
                    raise MemoryConflictError(f"Unknown memory: {memory_id}")
                connection.execute(
                    "DELETE FROM current_memories WHERE memory_id = ?", (memory_id,)
                )
                connection.execute(
                    "DELETE FROM memory_sync_issues WHERE memory_id = ?", (memory_id,)
                )
                connection.execute(
                    "DELETE FROM memory_events WHERE memory_id = ?", (memory_id,)
                )
            path = self.note_path(memory_id)
            if path.exists():
                path.unlink()
            if path.exists():
                raise MemoryMirrorError("Obsidian note could not be permanently removed")
            self._write_manifest_locked()
            return {"success": True, "id": memory_id, "purged": True}

    def history(self, memory_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_events WHERE memory_id = ? ORDER BY revision",
                (memory_id,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def list_active(self, *, target: str | None = None) -> list[dict[str, Any]]:
        sync = self.reconcile()
        blocked = {issue["memory_id"] for issue in sync["issues"]}
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event.*
                FROM current_memories current
                JOIN memory_events event ON event.event_id = current.event_id
                WHERE event.status = 'active'
                ORDER BY event.created_at
                """
            ).fetchall()
        records = []
        for row in rows:
            if row["memory_id"] in blocked:
                continue
            record = self._row_to_record(row)
            if target and record["scope"].get("target") != target:
                continue
            records.append(record)
        return records

    def resolve(self, selector: str, *, target: str | None = None) -> dict[str, Any]:
        selector = str(selector or "").strip()
        if not selector:
            raise MemoryConflictError("A memory id or unique content substring is required")
        records = self.list_active(target=target)
        exact = [record for record in records if record["id"] == selector]
        if exact:
            return exact[0]
        matches = [record for record in records if selector in record["content"]]
        if not matches:
            raise MemoryConflictError(f"No active memory matched: {selector}")
        if len(matches) > 1:
            raise MemoryConflictError(f"Multiple active memories matched: {selector}")
        return matches[0]

    def search(
        self,
        query: str,
        *,
        scope: dict[str, Any] | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        sync = self.reconcile()
        blocked = {issue["memory_id"] for issue in sync["issues"]}
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event.*
                FROM current_memories current
                JOIN memory_events event ON event.event_id = current.event_id
                WHERE event.status = 'active'
                """
            ).fetchall()
        query_terms = set(_TOKEN.findall((query or "").casefold()))
        ranked: list[tuple[int, float, sqlite3.Row]] = []
        for row in rows:
            if row["memory_id"] in blocked:
                continue
            row_scope = json.loads(row["scope_json"])
            if not self._scope_matches(row_scope, scope):
                continue
            content_lower = row["content"].casefold()
            content_terms = set(_TOKEN.findall(content_lower))
            overlap = len(query_terms & content_terms)
            phrase = 3 if query and query.casefold() in content_lower else 0
            if query_terms and overlap == 0 and not phrase:
                continue
            trust_score = {
                "explicit": 4,
                "user_edit": 4,
                "approved": 3,
                "mechanical": 3,
                "inferred": 1,
            }.get(row["trust"], 0)
            ranked.append((phrase + overlap * 2 + trust_score, row["created_at"], row))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return {
            "memories": [
                self._row_to_record(row) for _, _, row in ranked[: max(1, int(limit))]
            ],
            "sync": sync,
        }

    def why(self, memory_id: str) -> dict[str, Any]:
        row = self._require_current(memory_id)
        record = self._row_to_record(row)
        return {
            "id": memory_id,
            "revision": record["revision"],
            "trust": record["trust"],
            "source": record["source"],
            "scope": record["scope"],
            "content_hash": record["content_hash"],
            "note_path": record["note_path"],
        }

    def reconcile(self) -> dict[str, Any]:
        with self._exclusive():
            self._recover_pending_locked()
            with self._connect() as connection:
                connection.execute("DELETE FROM memory_sync_issues")
                current_rows = connection.execute(
                    """
                    SELECT event.*
                    FROM current_memories current
                    JOIN memory_events event ON event.event_id = current.event_id
                    ORDER BY event.memory_id
                    """
                ).fetchall()
            for row in current_rows:
                path = self.note_path(row["memory_id"])
                if not path.exists():
                    self._record_issue(
                        row["memory_id"], "note_missing", "Managed Obsidian note is missing."
                    )
                    continue
                try:
                    parsed = self._read_note(path)
                except (OSError, ValueError) as exc:
                    self._record_issue(row["memory_id"], "note_malformed", str(exc))
                    continue
                if parsed["memory_id"] != row["memory_id"]:
                    self._record_issue(
                        row["memory_id"],
                        "metadata_conflict",
                        "The note memory id does not match its filename.",
                    )
                    continue
                flags = self._threat_scan(parsed["content"])
                if flags:
                    self._record_issue(
                        row["memory_id"], "unsafe_content", ", ".join(flags)
                    )
                    continue
                if parsed["content"] != row["content"]:
                    self._commit_revision_locked(
                        memory_id=row["memory_id"],
                        revision=int(row["revision"]) + 1,
                        operation="obsidian_edit",
                        content=parsed["content"],
                        memory_type=parsed.get("memory_type") or row["memory_type"],
                        trust="user_edit",
                        scope=json.loads(row["scope_json"]),
                        source={
                            "kind": "obsidian",
                            "path": str(path),
                            "previous_revision": int(row["revision"]),
                        },
                        supersedes_revision=int(row["revision"]),
                    )
                    continue
                if (
                    int(parsed["revision"]) != int(row["revision"])
                    or parsed["content_hash"] != row["content_hash"]
                ):
                    self._record_issue(
                        row["memory_id"],
                        "metadata_conflict",
                        "The note revision or content hash differs from the ledger.",
                    )
            return self._status_without_reconcile()

    def sync_status(self) -> dict[str, Any]:
        self.reconcile()
        return self._status_without_reconcile()

    def _status_without_reconcile(self) -> dict[str, Any]:
        with self._connect() as connection:
            pending = connection.execute(
                "SELECT COUNT(*) FROM memory_events WHERE status = 'pending'"
            ).fetchone()[0]
            issues = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT memory_id, reason, detail, observed_at
                    FROM memory_sync_issues ORDER BY memory_id
                    """
                ).fetchall()
            ]
        return {
            "healthy": pending == 0 and not issues,
            "pending_events": int(pending),
            "issues": issues,
        }

    def _commit_revision_locked(
        self,
        *,
        memory_id: str,
        revision: int,
        operation: str,
        content: str,
        memory_type: str,
        trust: str,
        scope: dict[str, Any],
        source: dict[str, Any],
        supersedes_revision: int | None,
        active: bool = True,
    ) -> dict[str, Any]:
        event_id = str(uuid.uuid4())
        content_hash = _content_hash(content)
        path = self.note_path(memory_id)
        event = {
            "event_id": event_id,
            "memory_id": memory_id,
            "revision": int(revision),
            "operation": operation,
            "status": "pending",
            "content": content,
            "memory_type": memory_type,
            "scope_json": _json(scope),
            "trust": trust,
            "source_json": _json(source),
            "content_hash": content_hash,
            "note_path": str(path),
            "created_at": time.time(),
            "supersedes_revision": supersedes_revision,
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_events(
                    event_id, memory_id, revision, operation, status, content,
                    memory_type, scope_json, trust, source_json, content_hash,
                    note_path, created_at, supersedes_revision
                ) VALUES (
                    :event_id, :memory_id, :revision, :operation, :status, :content,
                    :memory_type, :scope_json, :trust, :source_json, :content_hash,
                    :note_path, :created_at, :supersedes_revision
                )
                """,
                event,
            )
        try:
            self._write_note(event, active=active)
            parsed = self._read_note(path)
            if (
                parsed["memory_id"] != memory_id
                or int(parsed["revision"]) != int(revision)
                or parsed["content_hash"] != content_hash
                or parsed["content"] != content
            ):
                raise MemoryMirrorError("Obsidian note readback did not match the event")
            self._write_manifest_locked(candidate=event if active else None, hidden_id=None if active else memory_id)
            self._verify_manifest_entry(memory_id, event if active else None)
            self._activate_event(event_id, memory_id, revision, active=active)
        except Exception as exc:
            if isinstance(exc, MemoryMirrorError):
                raise
            raise MemoryMirrorError(f"Memory stayed pending: {exc}") from exc
        row = self._event_row(event_id)
        return self._row_to_record(row)

    def _activate_event(
        self, event_id: str, memory_id: str, revision: int, *, active: bool
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT event_id FROM current_memories WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
            if current is not None:
                connection.execute(
                    "UPDATE memory_events SET status = 'superseded' WHERE event_id = ?",
                    (current["event_id"],),
                )
            connection.execute(
                "UPDATE memory_events SET status = ? WHERE event_id = ?",
                ("active" if active else "hidden", event_id),
            )
            if active:
                connection.execute(
                    """
                    INSERT INTO current_memories(memory_id, event_id, revision)
                    VALUES (?, ?, ?)
                    ON CONFLICT(memory_id) DO UPDATE SET
                        event_id = excluded.event_id,
                        revision = excluded.revision
                    """,
                    (memory_id, event_id, revision),
                )
            else:
                connection.execute(
                    "DELETE FROM current_memories WHERE memory_id = ?", (memory_id,)
                )
            connection.commit()

    def _recover_pending_locked(self) -> None:
        with self._connect() as connection:
            pending = connection.execute(
                "SELECT * FROM memory_events WHERE status = 'pending' ORDER BY created_at"
            ).fetchall()
        for row in pending:
            path = self.note_path(row["memory_id"])
            if not path.exists():
                continue
            try:
                parsed = self._read_note(path)
                manifest = self._read_manifest()
            except (OSError, ValueError):
                continue
            manifest_entry = manifest.get("memories", {}).get(row["memory_id"])
            note_matches = (
                parsed["memory_id"] == row["memory_id"]
                and int(parsed["revision"]) == int(row["revision"])
                and parsed["content"] == row["content"]
                and parsed["content_hash"] == row["content_hash"]
            )
            active_manifest_matches = (
                isinstance(manifest_entry, dict)
                and int(manifest_entry.get("revision", -1)) == int(row["revision"])
                and manifest_entry.get("content_hash") == row["content_hash"]
            )
            hidden_manifest_matches = (
                row["operation"] == "forget"
                and parsed.get("status") == "hidden"
                and manifest_entry is None
            )
            if note_matches and (active_manifest_matches or hidden_manifest_matches):
                self._activate_event(
                    row["event_id"],
                    row["memory_id"],
                    int(row["revision"]),
                    active=not hidden_manifest_matches,
                )

    def _write_note(self, event: dict[str, Any], *, active: bool = True) -> None:
        path = self.note_path(event["memory_id"])
        source = json.loads(event["source_json"])
        scope = json.loads(event["scope_json"])
        frontmatter = {
            "hermes_memory_id": event["memory_id"],
            "revision": int(event["revision"]),
            "type": event["memory_type"],
            "trust": event["trust"],
            "status": "active" if active else "hidden",
            "content_hash": event["content_hash"],
            "scope_json": _json(scope),
            "source_json": _json(source),
        }
        lines = ["---"]
        lines.extend(f"{key}: {value}" for key, value in frontmatter.items())
        lines.extend(["---", "", "# Memory", "", event["content"], ""])
        self._atomic_write(path, "\n".join(lines))

    def _read_note(self, path: Path) -> dict[str, Any]:
        raw = path.read_text(encoding="utf-8")
        match = _FRONTMATTER.match(raw)
        if not match:
            raise ValueError("Missing managed memory frontmatter")
        metadata: dict[str, str] = {}
        for line in match.group(1).splitlines():
            if ":" not in line:
                raise ValueError("Malformed managed memory frontmatter")
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip()
        required = {
            "hermes_memory_id",
            "revision",
            "type",
            "trust",
            "status",
            "content_hash",
        }
        if not required.issubset(metadata):
            raise ValueError("Incomplete managed memory frontmatter")
        body = raw[match.end() :].lstrip("\n")
        prefix = "# Memory\n\n"
        if not body.startswith(prefix):
            raise ValueError("Managed memory body is malformed")
        content = body[len(prefix) :].rstrip("\n")
        return {
            "memory_id": metadata["hermes_memory_id"],
            "revision": int(metadata["revision"]),
            "memory_type": metadata["type"],
            "trust": metadata["trust"],
            "status": metadata["status"],
            "content_hash": metadata["content_hash"],
            "content": content,
        }

    def _write_manifest_locked(
        self,
        *,
        candidate: dict[str, Any] | None = None,
        hidden_id: str | None = None,
    ) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event.*
                FROM current_memories current
                JOIN memory_events event ON event.event_id = current.event_id
                ORDER BY event.memory_id
                """
            ).fetchall()
        memories = {
            row["memory_id"]: {
                "revision": int(row["revision"]),
                "content_hash": row["content_hash"],
                "note_path": row["note_path"],
            }
            for row in rows
            if row["memory_id"] != hidden_id
        }
        if candidate is not None:
            memories[candidate["memory_id"]] = {
                "revision": int(candidate["revision"]),
                "content_hash": candidate["content_hash"],
                "note_path": candidate["note_path"],
            }
        payload = {"schema_version": SCHEMA_VERSION, "memories": memories}
        self._atomic_write(self.manifest_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")

    def _read_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {"schema_version": SCHEMA_VERSION, "memories": {}}
        value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("memories"), dict):
            raise ValueError("Malformed memory manifest")
        return value

    def _verify_manifest_entry(
        self, memory_id: str, event: dict[str, Any] | None
    ) -> None:
        manifest = self._read_manifest()
        entry = manifest["memories"].get(memory_id)
        if event is None:
            if entry is not None:
                raise MemoryMirrorError("Manifest still contains hidden memory")
            return
        if not isinstance(entry, dict):
            raise MemoryMirrorError("Manifest is missing the memory")
        if (
            int(entry.get("revision", -1)) != int(event["revision"])
            or entry.get("content_hash") != event["content_hash"]
        ):
            raise MemoryMirrorError("Manifest readback did not match the event")

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            atomic_replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def _record_issue(self, memory_id: str, reason: str, detail: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_sync_issues(memory_id, reason, detail, observed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    reason = excluded.reason,
                    detail = excluded.detail,
                    observed_at = excluded.observed_at
                """,
                (memory_id, reason, detail, time.time()),
            )

    def _current_row(self, memory_id: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT event.*
                FROM current_memories current
                JOIN memory_events event ON event.event_id = current.event_id
                WHERE current.memory_id = ?
                """,
                (memory_id,),
            ).fetchone()

    def _require_current(self, memory_id: str) -> sqlite3.Row:
        row = self._current_row(memory_id)
        if row is None:
            raise MemoryConflictError(f"Unknown or inactive memory: {memory_id}")
        return row

    def _latest_row(self, memory_id: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT * FROM memory_events
                WHERE memory_id = ? ORDER BY revision DESC LIMIT 1
                """,
                (memory_id,),
            ).fetchone()

    def _event_row(self, event_id: str) -> sqlite3.Row:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_events WHERE event_id = ?", (event_id,)
            ).fetchone()
        if row is None:  # pragma: no cover - internal integrity guard
            raise MemoryConflictError(f"Missing event: {event_id}")
        return row

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["memory_id"],
            "event_id": row["event_id"],
            "revision": int(row["revision"]),
            "operation": row["operation"],
            "status": row["status"],
            "content": row["content"],
            "memory_type": row["memory_type"],
            "scope": json.loads(row["scope_json"]),
            "trust": row["trust"],
            "source": json.loads(row["source_json"]),
            "content_hash": row["content_hash"],
            "note_path": row["note_path"],
            "created_at": row["created_at"],
        }

    def _validate_content(self, content: str) -> str:
        content = str(content or "").strip()
        if not content:
            raise ValueError("Memory content cannot be empty")
        flags = self._threat_scan(content)
        if flags:
            raise ValueError(f"Unsafe memory content: {', '.join(flags)}")
        return content

    @staticmethod
    def _scope_matches(
        memory_scope: dict[str, Any], requested_scope: dict[str, Any] | None
    ) -> bool:
        if memory_scope.get("kind") == "global" or not requested_scope:
            return True
        for key in ("profile", "project", "source", "thread"):
            expected = memory_scope.get(key)
            if expected is not None and requested_scope.get(key) != expected:
                return False
        return True


class MemorySyncWorker:
    """Small dependency-free polling worker; retrieval still performs a hash fence."""

    def __init__(self, repository: ReliableMemoryRepository, interval: float = 2.0):
        self.repository = repository
        self.interval = max(0.1, float(interval))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="hermes-memory-sync", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.repository.reconcile()
            except Exception:
                # The retrieval fence and sync status surface persistent failures.
                continue
