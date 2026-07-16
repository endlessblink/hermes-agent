"""Local semantic index for exact conversation rows archived by compaction.

The exact transcript in ``messages`` remains authoritative.  This module keeps
only bounded, derived chunks plus local Ollama embeddings and source pointers.
It deliberately depends on the small ``SessionDB`` surface already available
to the agent and fails open when the local embedding service is unavailable.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import math
import os
import struct
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_EMBED_MODEL = "nomic-embed-text"
DEFAULT_MAX_CHUNK_CHARS = 2_400
DEFAULT_MAX_CHUNKS_PER_BATCH = 16
DEFAULT_MAX_BACKGROUND_BATCHES = 32
DEFAULT_MAX_RESULTS = 4
DEFAULT_MAX_SEARCH_CANDIDATES = 1_000

_COMPACTION_MARKERS = (
    "[CONTEXT COMPACTION",
    "[CONTEXT SUMMARY]",
    "--- END OF CONTEXT SUMMARY",
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS archived_semantic_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    first_message_id INTEGER NOT NULL,
    last_message_id INTEGER NOT NULL,
    citation TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    dims INTEGER NOT NULL,
    vector BLOB NOT NULL,
    indexed_at REAL NOT NULL DEFAULT (unixepoch('subsec')),
    UNIQUE(session_id, first_message_id, last_message_id, content_hash, model)
);
CREATE INDEX IF NOT EXISTS idx_archived_semantic_chunks_session_model
    ON archived_semantic_chunks(session_id, model, id);
"""

EmbedCallable = Callable[[list[str], str], list[list[float]]]


class OllamaEmbedder:
    """Small stdlib-only client for Ollama's batch ``/api/embed`` endpoint."""

    def __init__(
        self,
        base_url: str = DEFAULT_OLLAMA_URL,
        *,
        timeout: float = 5.0,
    ) -> None:
        raw_url = (base_url or DEFAULT_OLLAMA_URL).strip().rstrip("/")
        parsed = urllib.parse.urlparse(raw_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Ollama embed URL must be an HTTP localhost URL")
        hostname = parsed.hostname.casefold()
        is_local = hostname == "localhost"
        if not is_local:
            try:
                is_local = ipaddress.ip_address(hostname).is_loopback
            except ValueError:
                is_local = False
        if not is_local:
            raise ValueError("Ollama embed URL must resolve to localhost")
        self.endpoint = (
            raw_url if raw_url.endswith("/api/embed") else raw_url + "/api/embed"
        )
        self.timeout = max(0.1, float(timeout))

    def __call__(self, texts: list[str], model: str) -> list[list[float]]:
        if not texts:
            return []
        payload = json.dumps(
            {"model": model or DEFAULT_EMBED_MODEL, "input": list(texts)},
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            decoded = json.loads(response.read().decode("utf-8"))
        vectors = decoded.get("embeddings") if isinstance(decoded, dict) else None
        return _validate_vectors(vectors, expected=len(texts))


@dataclass(frozen=True)
class _Chunk:
    session_id: str
    first_message_id: int
    last_message_id: int
    citation: str
    content: str
    content_hash: str


@dataclass
class BackgroundIndexJob:
    """Observable handle for one daemon indexing batch."""

    _done: threading.Event = field(default_factory=threading.Event)
    indexed_count: int = 0
    error: BaseException | None = None
    thread: threading.Thread | None = None

    def wait(self, timeout: float | None = None) -> int:
        if not self._done.wait(timeout):
            raise TimeoutError("semantic history indexing did not finish in time")
        return self.indexed_count


class LocalSemanticHistoryIndex:
    """Bounded semantic cache over compacted rows in one conversation lineage."""

    def __init__(
        self,
        db: Any,
        *,
        ollama_url: str | None = None,
        model: str | None = None,
        timeout: float = 5.0,
        embed: EmbedCallable | None = None,
        max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
        max_chunks_per_batch: int = DEFAULT_MAX_CHUNKS_PER_BATCH,
        max_background_batches: int = DEFAULT_MAX_BACKGROUND_BATCHES,
        max_results: int = DEFAULT_MAX_RESULTS,
        max_search_candidates: int = DEFAULT_MAX_SEARCH_CANDIDATES,
    ) -> None:
        self.db = db
        self.model = (
            model
            or os.environ.get("HERMES_SEMANTIC_EMBED_MODEL")
            or DEFAULT_EMBED_MODEL
        )
        self.max_chunk_chars = max(80, min(int(max_chunk_chars), 8_000))
        self.max_chunks_per_batch = max(1, min(int(max_chunks_per_batch), 128))
        self.max_background_batches = max(
            1, min(int(max_background_batches), 128)
        )
        self.max_results = max(1, min(int(max_results), 12))
        self.max_search_candidates = max(
            self.max_results,
            min(int(max_search_candidates), 5_000),
        )
        self._embed = embed or OllamaEmbedder(
            ollama_url
            or os.environ.get("HERMES_SEMANTIC_OLLAMA_URL")
            or DEFAULT_OLLAMA_URL,
            timeout=timeout,
        )

    def index_batch(self, session_id: str) -> int:
        """Synchronously index one bounded batch of not-yet-indexed chunks.

        Production callers should invoke this through
        :meth:`start_background_index`; the synchronous form is deterministic
        for workers, maintenance commands, and tests.
        """
        if not session_id or not self._ensure_schema():
            return 0
        try:
            lineage = self._lineage(session_id)
            chunks = self._build_chunks(lineage)
            pending = self._pending_chunks(chunks)[: self.max_chunks_per_batch]
            if not pending:
                return 0
            vectors = _validate_vectors(
                self._embed([chunk.content for chunk in pending], self.model),
                expected=len(pending),
            )
            return self._store_chunks(pending, vectors)
        except Exception:
            logger.debug("semantic history indexing failed", exc_info=True)
            return 0

    def start_background_index(self, session_id: str) -> BackgroundIndexJob:
        """Start one bounded Ollama indexing batch on a daemon thread."""
        job = BackgroundIndexJob()

        def _run() -> None:
            try:
                total = 0
                for _ in range(self.max_background_batches):
                    indexed = self._index_batch_with_error(session_id)
                    total += indexed
                    if indexed < self.max_chunks_per_batch:
                        break
                job.indexed_count = total
            except BaseException as exc:  # captured for diagnostics; turn stays healthy
                job.error = exc
                job.indexed_count = 0
            finally:
                job._done.set()

        thread = threading.Thread(
            target=_run,
            name="hermes-semantic-history-index",
            daemon=True,
        )
        job.thread = thread
        thread.start()
        return job

    def search(
        self,
        session_id: str,
        query: str,
        *,
        limit: int = DEFAULT_MAX_RESULTS,
    ) -> list[dict[str, Any]]:
        """Synchronously rank already-indexed chunks in the current lineage."""
        if not session_id or not isinstance(query, str) or not query.strip():
            return []
        if not self._ensure_schema():
            return []
        try:
            lineage = self._lineage(session_id)
            rows = self._indexed_rows(lineage)
            if not rows:
                return []
            query_vectors = _validate_vectors(
                self._embed([query.strip()], self.model), expected=1
            )
            query_vector = query_vectors[0]
            scored: list[tuple[float, int, dict[str, Any]]] = []
            for row in rows:
                dims = int(row["dims"])
                if dims != len(query_vector):
                    continue
                vector = list(struct.unpack(f"<{dims}f", row["vector"]))
                score = _cosine_similarity(query_vector, vector)
                hit = {
                    "session_id": row["session_id"],
                    "first_message_id": row["first_message_id"],
                    "last_message_id": row["last_message_id"],
                    "citation": row["citation"],
                    "content": row["content"],
                    "content_hash": row["content_hash"],
                    "model": row["model"],
                    "score": round(score, 6),
                }
                scored.append((score, int(row["id"]), hit))
            scored.sort(key=lambda item: (-item[0], item[1]))
            bounded_limit = max(1, min(int(limit), self.max_results))
            return [item[2] for item in scored[:bounded_limit]]
        except Exception:
            logger.debug("semantic history search failed", exc_info=True)
            return []

    def _index_batch_with_error(self, session_id: str) -> int:
        """Index like :meth:`index_batch`, preserving errors for job diagnostics."""
        if not session_id or not self._ensure_schema():
            return 0
        lineage = self._lineage(session_id)
        chunks = self._build_chunks(lineage)
        pending = self._pending_chunks(chunks)[: self.max_chunks_per_batch]
        if not pending:
            return 0
        vectors = _validate_vectors(
            self._embed([chunk.content for chunk in pending], self.model),
            expected=len(pending),
        )
        return self._store_chunks(pending, vectors)

    def _ensure_schema(self) -> bool:
        try:
            with self.db._lock:
                self.db._conn.executescript(_SCHEMA_SQL)
            return True
        except Exception:
            logger.debug("semantic history schema unavailable", exc_info=True)
            return False

    def _lineage(self, session_id: str) -> list[str]:
        try:
            lineage = self.db.get_compression_lineage(session_id)
        except Exception:
            lineage = None
        cleaned = [str(item) for item in (lineage or []) if item]
        return cleaned or [session_id]

    def _build_chunks(self, lineage: Sequence[str]) -> list[_Chunk]:
        placeholders = ",".join("?" for _ in lineage)
        if not placeholders:
            return []
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT id, session_id, role, content, tool_name "
                f"FROM messages WHERE session_id IN ({placeholders}) "
                "AND active = 0 AND compacted = 1 ORDER BY id",
                tuple(lineage),
            ).fetchall()

        chunks: list[_Chunk] = []
        current: list[tuple[str, int, str]] = []
        current_chars = 0
        current_session = ""

        def flush() -> None:
            nonlocal current, current_chars, current_session
            if current:
                chunks.append(self._make_chunk(current_session, current))
            current = []
            current_chars = 0
            current_session = ""

        for row in rows:
            session = str(row["session_id"])
            role = str(row["role"] or "message")
            text = self._row_text(row["content"])
            if not text or any(marker in text for marker in _COMPACTION_MARKERS):
                continue
            tool_suffix = f" ({row['tool_name']})" if row["tool_name"] else ""
            prefix = f"{role}{tool_suffix}: "
            message_id = int(row["id"])
            pieces = _split_bounded(prefix, text, self.max_chunk_chars)
            for piece in pieces:
                starts_new_turn = role == "user" and bool(current)
                crosses_session = bool(current) and current_session != session
                projected = current_chars + (1 if current else 0) + len(piece)
                if starts_new_turn or crosses_session or projected > self.max_chunk_chars:
                    flush()
                if not current_session:
                    current_session = session
                current.append((piece, message_id, role))
                current_chars += (1 if current_chars else 0) + len(piece)
        flush()
        return chunks

    @staticmethod
    def _row_text(raw: Any) -> str:
        try:
            if isinstance(raw, str):
                decoded = json.loads(raw)
                if isinstance(decoded, list):
                    text = " ".join(
                        str(part.get("text") or "")
                        for part in decoded
                        if isinstance(part, dict) and part.get("type") == "text"
                    ).strip()
                    return text
            return str(raw or "").strip()
        except (TypeError, json.JSONDecodeError):
            return str(raw or "").strip()

    @staticmethod
    def _make_chunk(session_id: str, entries: Sequence[tuple[str, int, str]]) -> _Chunk:
        content = "\n".join(entry[0] for entry in entries)
        first_id = min(entry[1] for entry in entries)
        last_id = max(entry[1] for entry in entries)
        return _Chunk(
            session_id=session_id,
            first_message_id=first_id,
            last_message_id=last_id,
            citation=f"session:{session_id}#messages:{first_id}-{last_id}",
            content=content,
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )

    def _pending_chunks(self, chunks: Sequence[_Chunk]) -> list[_Chunk]:
        if not chunks:
            return []
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT session_id, first_message_id, last_message_id, content_hash "
                "FROM archived_semantic_chunks WHERE model = ?",
                (self.model,),
            ).fetchall()
        existing = {
            (
                row["session_id"],
                int(row["first_message_id"]),
                int(row["last_message_id"]),
                row["content_hash"],
            )
            for row in rows
        }
        return [
            chunk
            for chunk in chunks
            if (
                chunk.session_id,
                chunk.first_message_id,
                chunk.last_message_id,
                chunk.content_hash,
            )
            not in existing
        ]

    def _store_chunks(
        self,
        chunks: Sequence[_Chunk],
        vectors: Sequence[Sequence[float]],
    ) -> int:
        if not chunks:
            return 0
        inserted = 0
        with self.db._lock:
            for chunk, vector in zip(chunks, vectors):
                blob = struct.pack(f"<{len(vector)}f", *vector)
                cursor = self.db._conn.execute(
                    "INSERT OR IGNORE INTO archived_semantic_chunks "
                    "(session_id, first_message_id, last_message_id, citation, content, "
                    "content_hash, model, dims, vector) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        chunk.session_id,
                        chunk.first_message_id,
                        chunk.last_message_id,
                        chunk.citation,
                        chunk.content,
                        chunk.content_hash,
                        self.model,
                        len(vector),
                        blob,
                    ),
                )
                inserted += max(0, int(cursor.rowcount))
        return inserted

    def _indexed_rows(self, lineage: Sequence[str]) -> list[Any]:
        placeholders = ",".join("?" for _ in lineage)
        if not placeholders:
            return []
        with self.db._lock:
            return self.db._conn.execute(
                "SELECT id, session_id, first_message_id, last_message_id, citation, "
                "content, content_hash, model, dims, vector "
                f"FROM archived_semantic_chunks WHERE session_id IN ({placeholders}) "
                "AND model = ? ORDER BY id DESC LIMIT ?",
                (*lineage, self.model, self.max_search_candidates),
            ).fetchall()


def index_compacted_history(
    db: Any,
    session_id: str,
    **options: Any,
) -> int:
    """Deterministic synchronous indexing API for workers and maintenance."""
    return LocalSemanticHistoryIndex(db, **options).index_batch(session_id)


def search_semantic_history(
    db: Any,
    session_id: str,
    query: str,
    *,
    limit: int = DEFAULT_MAX_RESULTS,
    **options: Any,
) -> list[dict[str, Any]]:
    """Deterministic synchronous search API for turn-context integration."""
    return LocalSemanticHistoryIndex(db, **options).search(
        session_id, query, limit=limit
    )


def start_background_history_index(
    db: Any,
    session_id: str,
    **options: Any,
) -> BackgroundIndexJob:
    """Convenience API that keeps Ollama indexing off the turn critical path."""
    return LocalSemanticHistoryIndex(db, **options).start_background_index(session_id)


def _split_bounded(prefix: str, text: str, max_chars: int) -> list[str]:
    available = max(1, max_chars - len(prefix))
    if len(text) <= available:
        return [prefix + text]
    return [prefix + text[start : start + available] for start in range(0, len(text), available)]


def _validate_vectors(raw: Any, *, expected: int) -> list[list[float]]:
    if not isinstance(raw, list) or len(raw) != expected:
        raise ValueError("embedding response count did not match input count")
    vectors: list[list[float]] = []
    dims: int | None = None
    for item in raw:
        if not isinstance(item, list) or not item:
            raise ValueError("embedding response contained an empty vector")
        vector = [float(value) for value in item]
        if any(not math.isfinite(value) for value in vector):
            raise ValueError("embedding response contained a non-finite value")
        if dims is None:
            dims = len(vector)
        elif len(vector) != dims:
            raise ValueError("embedding response dimensions were inconsistent")
        vectors.append(vector)
    return vectors


def _cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    left_values = list(left)
    right_values = list(right)
    if len(left_values) != len(right_values) or not left_values:
        return 0.0
    dot = sum(a * b for a, b in zip(left_values, right_values))
    left_norm = math.sqrt(sum(value * value for value in left_values))
    right_norm = math.sqrt(sum(value * value for value in right_values))
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


__all__ = [
    "BackgroundIndexJob",
    "DEFAULT_EMBED_MODEL",
    "DEFAULT_OLLAMA_URL",
    "LocalSemanticHistoryIndex",
    "OllamaEmbedder",
    "index_compacted_history",
    "search_semantic_history",
    "start_background_history_index",
]
