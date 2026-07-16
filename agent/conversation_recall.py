"""Bounded automatic recall from exact rows archived by context compaction."""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from typing import Any

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[^\W_]+(?:[-.][^\W_]+)*", re.UNICODE)
_STOPWORDS = {
    "about", "after", "again", "also", "and", "are", "can", "could",
    "did", "does", "for", "from", "have", "how", "into", "just", "like",
    "more", "our", "please", "remind", "should", "that", "the", "then",
    "there", "these", "they", "this", "was", "what", "when", "where",
    "which", "with", "would", "you", "your",
    "אבל", "אני", "אם", "את", "זה", "זאת", "כדי", "כל", "לא", "מה",
    "על", "עם", "של", "שהוא", "שוב",
}
_VAGUE_ONLY = {
    "continue", "go", "next", "proceed", "resume", "well", "yes",
    "המשך", "כן", "קדימה",
}
_COMPACTION_MARKERS = (
    "[CONTEXT COMPACTION",
    "[CONTEXT SUMMARY]",
    "--- END OF CONTEXT SUMMARY",
)
_SEMANTIC_JOBS: dict[str, Any] = {}
_SEMANTIC_JOBS_LOCK = threading.Lock()


def _candidate_tokens(text: str, *, limit: int = 8) -> list[str]:
    seen: set[str] = set()
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text or ""):
        token = raw.casefold().strip(".-")
        if len(token) < 3 or token in _STOPWORDS or token in _VAGUE_ONLY or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
        if len(tokens) >= limit:
            break
    return tokens


def extract_recall_query(user_message: str, fallback_text: str = "") -> str:
    """Build a conservative OR query from distinctive current-turn terms."""
    tokens = _candidate_tokens(user_message)
    if len(tokens) < 2:
        tokens = _candidate_tokens(fallback_text)
    if len(tokens) < 2:
        return ""
    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


def _text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(
            str(part.get("text") or "")
            for part in value
            if isinstance(part, dict) and part.get("type") == "text"
        ).strip()
    return str(value or "")


def _clean_content(value: Any, *, role: str) -> str:
    text = _text_content(value).strip()
    if not text or any(marker in text for marker in _COMPACTION_MARKERS):
        return ""
    cap = 700 if role == "tool" else 1_200
    if len(text) > cap:
        text = text[: cap - 32].rstrip() + "\n[archived content truncated]"
    return text


def build_archived_conversation_context(
    db: Any,
    *,
    session_id: str,
    user_message: str,
    fallback_query_context: str = "",
    max_chars: int = 6_000,
    max_hits: int = 4,
) -> str:
    """Return cited, deduplicated archive snippets for API-only injection.

    Failures are deliberately silent: archived recall improves continuity but
    must never prevent the live turn from running.
    """
    if db is None or not session_id or max_chars <= 0 or max_hits <= 0:
        return ""
    try:
        lineage = db.get_compression_lineage(session_id) or [session_id]
        if not any(db.has_archived_messages(lineage_id) for lineage_id in lineage):
            return ""
        query = extract_recall_query(user_message, fallback_query_context)
        if not query:
            return ""
        lexical_hits = db.search_compacted_messages(
            lineage,
            query,
            role_filter=["user", "assistant", "tool"],
            limit=max_hits * 3,
            context_window=1,
        )
    except Exception:
        logger.debug("archived conversation recall failed", exc_info=True)
        return ""

    semantic_hits: list[dict[str, Any]] = []
    if hasattr(db, "_lock") and hasattr(db, "_conn"):
        semantic_query = (user_message or fallback_query_context or "").strip()
        try:
            from agent.semantic_history_index import search_semantic_history

            semantic_hits = [
                hit
                for hit in search_semantic_history(
                    db,
                    session_id,
                    semantic_query,
                    limit=max_hits,
                    timeout=1.25,
                )
                if float(hit.get("score", 0.0) or 0.0) >= 0.35
            ]
        except Exception:
            logger.debug("semantic conversation recall failed", exc_info=True)

        # Indexing is explicitly off the turn critical path. One worker per
        # session drains bounded Ollama batches; a later turn reuses the cache.
        try:
            from agent.semantic_history_index import start_background_history_index

            with _SEMANTIC_JOBS_LOCK:
                prior = _SEMANTIC_JOBS.get(session_id)
                prior_done = prior is None or prior._done.is_set()
                if prior_done:
                    _SEMANTIC_JOBS[session_id] = start_background_history_index(
                        db,
                        session_id,
                        timeout=60.0,
                    )
        except Exception:
            logger.debug("semantic conversation indexing failed to start", exc_info=True)

    hits: list[dict[str, Any]] = []
    for index in range(max(len(lexical_hits), len(semantic_hits))):
        if index < len(lexical_hits):
            hits.append(lexical_hits[index])
        if index < len(semantic_hits):
            semantic = semantic_hits[index]
            hits.append(
                {
                    "id": semantic.get("first_message_id"),
                    "session_id": semantic.get("session_id"),
                    "timestamp": None,
                    "semantic_citation": semantic.get("citation"),
                    "semantic_score": semantic.get("score"),
                    "context": [
                        {
                            "role": "archived semantic chunk",
                            "content": semantic.get("content"),
                        }
                    ],
                }
            )

    header = (
        "## Archived conversation recall\n"
        "Exact earlier turns retrieved from this conversation's compacted history. "
        "Reference only; the current user message remains authoritative. "
        "When archived statements conflict, prefer the newest cited correction.\n"
    )
    parts = [header]
    used_chars = len(header)
    seen_neighborhoods: set[str] = set()
    accepted = 0

    for hit in hits:
        rows = hit.get("context") if isinstance(hit.get("context"), list) else [hit]
        rendered_rows: list[str] = []
        fingerprint_parts: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            role = str(row.get("role") or "message")
            content = _clean_content(row.get("content"), role=role)
            if not content:
                continue
            fingerprint_parts.append(f"{role}:{' '.join(content.casefold().split())}")
            tool_suffix = f" ({row.get('tool_name')})" if row.get("tool_name") else ""
            rendered_rows.append(f"- {role}{tool_suffix}: {content}")
        if not rendered_rows:
            continue
        fingerprint = hashlib.sha256("\n".join(fingerprint_parts).encode("utf-8")).hexdigest()
        if fingerprint in seen_neighborhoods:
            continue
        seen_neighborhoods.add(fingerprint)

        if hit.get("semantic_citation"):
            citation = (
                f"[{hit.get('semantic_citation')} semantic_score="
                f"{float(hit.get('semantic_score') or 0.0):.3f}]"
            )
        else:
            citation = (
                f"[session={hit.get('session_id')} message={hit.get('id')} "
                f"timestamp={hit.get('timestamp')}]"
            )
        block = citation + "\n" + "\n".join(rendered_rows) + "\n"
        if used_chars + len(block) > max_chars:
            remaining = max_chars - used_chars
            if remaining > len(citation) + 80:
                parts.append(block[:remaining].rstrip())
            break
        parts.append(block)
        used_chars += len(block)
        accepted += 1
        if accepted >= max_hits:
            break

    return "\n".join(parts).strip() if accepted or len(parts) > 1 else ""
