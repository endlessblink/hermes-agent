"""Read-only filesystem adapter and keyword retrieval for Obsidian notes."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from .config import VaultConfig
from .path_policy import SourceReceipt, VaultAccessError, VaultBoundary, content_hash, detect_prompt_injection


WORD_RE = re.compile(r"[\w֐-׿]+", re.UNICODE)


def _extract_heading(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or None
    return None


def _terms(query: str) -> list[str]:
    return [part.lower() for part in WORD_RE.findall(query or "") if part.strip()]


def _line_snippet(line: str, max_chars: int = 240) -> str:
    compact = " ".join(line.strip().split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 1].rstrip() + "..."


def _best_snippet(text: str, terms: list[str]) -> tuple[str, int, list[str]]:
    best_line = ""
    best_score = 0
    best_matches: list[str] = []
    for line in text.splitlines():
        lowered = line.lower()
        matches = [term for term in terms if term in lowered]
        score = len(matches)
        if score > best_score:
            best_line = line
            best_score = score
            best_matches = matches
    if best_score == 0:
        lines = text.splitlines()
        return _line_snippet(lines[0] if lines else ""), 0, []
    return _line_snippet(best_line), best_score, best_matches


class VaultAccessAdapter:
    """Filesystem backend for read-only vault access."""

    def __init__(self, boundary: VaultBoundary):
        self.boundary = boundary

    def read_text(self, path: str) -> tuple[Path, str]:
        resolved = self.boundary.resolve_read_path(path)
        text = resolved.read_text(encoding="utf-8", errors="replace")
        return resolved, text

    def list_notes(self, folder: str | None = None) -> list[Path]:
        return list(self.boundary.iter_markdown_notes(folder))


class RetrievalService:
    """Keyword retrieval with source receipts and prompt-injection flags."""

    def __init__(self, config: VaultConfig):
        self.config = config
        self.boundary = VaultBoundary(config)
        self.adapter = VaultAccessAdapter(self.boundary)

    def status(self) -> dict[str, Any]:
        return {
            "success": True,
            "enabled": self.config.enabled,
            "backend": "filesystem",
            "mode": "read_only",
            "canonical_vault_root": str(self.boundary.vault_root),
            "visible_workspace": str(self.boundary.visible_workspace),
            "supports": {
                "list_notes": True,
                "read_note": True,
                "search_keyword": True,
                "writes": False,
                "index": False,
            },
        }

    def receipt_for(self, path: Path, text: str) -> SourceReceipt:
        flags = detect_prompt_injection(text)
        stat = path.stat()
        return SourceReceipt(
            path=self.boundary.relative_receipt_path(path),
            heading=_extract_heading(text),
            modified_time=stat.st_mtime,
            content_hash=content_hash(text),
            trust="untrusted_data" if flags else "vault_note_data",
            safety_flags=flags,
        )

    def list_notes(self, folder: str | None = None, prefix: str | None = None) -> dict[str, Any]:
        notes = []
        prefix_text = str(prefix or "").strip().lower()
        for path in self.adapter.list_notes(folder):
            rel = self.boundary.relative_receipt_path(path)
            if prefix_text and not rel.lower().startswith(prefix_text):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                receipt = self.receipt_for(path, text).to_dict()
            except OSError:
                continue
            notes.append(receipt)
        return {"success": True, "notes": notes, "count": len(notes)}

    def read_note(self, path: str) -> dict[str, Any]:
        resolved, text = self.adapter.read_text(path)
        if len(text) > self.config.max_read_chars:
            truncated = True
            returned = text[: self.config.max_read_chars]
        else:
            truncated = False
            returned = text
        receipt = self.receipt_for(resolved, text).to_dict()
        return {
            "success": True,
            "content": returned,
            "truncated": truncated,
            "receipt": receipt,
            "note_text_is_untrusted_data": True,
        }

    def search_keyword(self, query: str, filters: Mapping[str, Any] | None = None) -> dict[str, Any]:
        query_text = str(query or "").strip()
        terms = _terms(query_text)
        if not terms:
            raise VaultAccessError("empty_query", "Search query is required.")

        filters = filters or {}
        folder = filters.get("folder") if isinstance(filters, Mapping) else None
        limit = filters.get("limit") if isinstance(filters, Mapping) else None
        try:
            max_results = int(limit) if limit else self.config.max_search_results
        except (TypeError, ValueError):
            max_results = self.config.max_search_results
        max_results = max(1, min(max_results, self.config.max_search_results))

        results = []
        for path in self.adapter.list_notes(str(folder) if folder else None):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lowered = text.lower()
            matched_terms = [term for term in terms if term in lowered]
            if not matched_terms:
                continue
            snippet, line_score, line_matches = _best_snippet(text, terms)
            score = len(set(matched_terms)) * 10 + line_score
            receipt = self.receipt_for(path, text).to_dict()
            results.append(
                {
                    "receipt": receipt,
                    "snippet": snippet,
                    "score": score,
                    "match_reason": {
                        "type": "keyword",
                        "matched_terms": sorted(set(matched_terms)),
                        "snippet_terms": sorted(set(line_matches)),
                    },
                }
            )

        results.sort(key=lambda item: (-int(item["score"]), item["receipt"]["path"]))
        return {
            "success": True,
            "query": query_text,
            "results": results[:max_results],
            "count": min(len(results), max_results),
            "total_matches": len(results),
            "backend": "keyword_scan",
        }
