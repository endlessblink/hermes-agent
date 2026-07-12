"""Obsidian source-of-truth adapter for durable assistant understanding."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agent.vault_knowledge.config import load_vault_config
from agent.vault_knowledge.path_policy import VaultBoundary, content_hash

NOTE_PATH = "_System/Hermes Knowledge Graph/Office Work Personal Assistant.md"
DURABLE_SECTIONS = ("outcomes", "commitments", "preferences")
_HEADING = {"outcomes": "Outcomes", "commitments": "Commitments", "preferences": "Preferences"}
_LINE = re.compile(r"^- \[([^\]]+)\] (.*?)(?: <!-- hermes-meta (\{.*\}) -->)?$")


class PersonalAssistantNoteError(ValueError):
    pass


class PersonalAssistantObsidianAdapter:
    def __init__(self, config: dict[str, Any] | None = None):
        cfg = load_vault_config(config)
        if not cfg.enabled:
            raise PersonalAssistantNoteError("Obsidian vault is disabled")
        self.boundary = VaultBoundary(cfg)
        self.boundary.validate_write_target(NOTE_PATH)
        self.path = self.boundary.visible_workspace / NOTE_PATH

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {**{section: [] for section in DURABLE_SECTIONS}, "archived": [], "sourceVersion": 0, "sourceHash": None}
        text = self.path.read_text(encoding="utf-8")
        parsed = self._parse(text)
        parsed["sourceHash"] = content_hash(text)
        return parsed

    def write(self, state: dict[str, Any], *, expected_hash: str | None = None) -> dict[str, Any]:
        current = self.read()
        if expected_hash is not None and current.get("sourceHash") != expected_hash:
            raise PersonalAssistantNoteError("Obsidian assistant note changed; refresh before editing")
        payload = {
            section: state.get(section, []) for section in DURABLE_SECTIONS
        }
        payload["archived"] = state.get("archived", [])
        version = int(current.get("sourceVersion") or 0) + 1
        text = self._render(payload, version)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Reuse the repository's symlink-safe atomic writer contract. JSON
        # wrapper is not suitable for Markdown, so stage adjacent and replace.
        import os
        from uuid import uuid4

        staged = self.path.parent / f".{self.path.name}.{uuid4().hex}.tmp"
        staged.write_text(text, encoding="utf-8")
        os.chmod(staged, 0o600)
        os.replace(staged, self.path)
        return self.read()

    @staticmethod
    def _parse(text: str) -> dict[str, Any]:
        version_match = re.search(r"^source_version:\s*(\d+)\s*$", text, re.MULTILINE)
        if not version_match:
            raise PersonalAssistantNoteError("Malformed assistant note: missing source_version")
        result = {section: [] for section in DURABLE_SECTIONS}
        result["archived"] = []
        result["sourceVersion"] = int(version_match.group(1))
        section = None
        heading_to_key = {**{value: key for key, value in _HEADING.items()}, "Archived": "archived"}
        for raw in text.splitlines():
            if raw.startswith("## "):
                section = heading_to_key.get(raw[3:].strip())
                continue
            if section is None or not raw.startswith("- "):
                continue
            match = _LINE.match(raw)
            if not match:
                raise PersonalAssistantNoteError(f"Malformed assistant note item: {raw[:120]}")
            item_id, title, metadata = match.groups()
            item = {"id": item_id, "title": title.strip()}
            if metadata:
                try:
                    extra = json.loads(metadata)
                except ValueError as exc:
                    raise PersonalAssistantNoteError("Malformed assistant note metadata") from exc
                if not isinstance(extra, dict):
                    raise PersonalAssistantNoteError("Malformed assistant note metadata")
                item.update(extra)
            result[section].append(item)
        return result

    @staticmethod
    def _render(state: dict[str, Any], version: int) -> str:
        lines = ["---", "type: office-work-personal-assistant", "schema_version: 1", f"source_version: {version}", "---", "", "# Office Work Personal Assistant", "", "This note stores durable understanding, not live task or schedule truth.", ""]
        for key, heading in [*[(key, _HEADING[key]) for key in DURABLE_SECTIONS], ("archived", "Archived")]:
            lines.extend([f"## {heading}", ""])
            for item in state.get(key, []):
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                title = str(item.get("title") or item.get("summary") or "Untitled").replace("\n", " ")
                metadata = {k: v for k, v in item.items() if k not in {"id", "title", "summary"}}
                suffix = f" <!-- hermes-meta {json.dumps(metadata, ensure_ascii=False, sort_keys=True)} -->" if metadata else ""
                lines.append(f"- [{item['id']}] {title}{suffix}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
