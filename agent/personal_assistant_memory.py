"""Project the editable Personal Assistant note into unified Hermes memory."""

from __future__ import annotations

import uuid
from typing import Any

from agent.personal_assistant_obsidian import DURABLE_SECTIONS
from agent.reliable_memory import ReliableMemoryRepository


_PERSONAL_ASSISTANT_NAMESPACE = uuid.UUID("fbc9fe5a-9781-4c25-a2b5-a4f600142f1b")


def personal_assistant_memory_id(section: str, item_id: str) -> str:
    return str(
        uuid.uuid5(
            _PERSONAL_ASSISTANT_NAMESPACE,
            f"{section.strip()}:{item_id.strip()}",
        )
    )


class PersonalAssistantMemoryProjector:
    """Keep durable PA items represented in the verified memory ledger."""

    def __init__(self, repository: ReliableMemoryRepository):
        self.repository = repository

    def sync(self, note: dict[str, Any], *, trust: str) -> dict[str, int]:
        desired: dict[str, tuple[str, dict[str, Any], str]] = {}
        for section in DURABLE_SECTIONS:
            for item in note.get(section) or []:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id") or "").strip()
                content = str(item.get("title") or item.get("summary") or "").strip()
                if not item_id or not content:
                    continue
                memory_id = personal_assistant_memory_id(section, item_id)
                desired[memory_id] = (section, dict(item), content)

        active = {
            record["id"]: record
            for record in self.repository.list_active(target="personal_assistant")
        }
        counts = {"added": 0, "updated": 0, "hidden": 0}

        for memory_id, record in active.items():
            if memory_id not in desired:
                self.repository.forget(
                    memory_id,
                    source={
                        "kind": "personal_assistant_note",
                        "note_hash": note.get("sourceHash"),
                        "note_version": note.get("sourceVersion"),
                    },
                )
                counts["hidden"] += 1

        for memory_id, (section, item, content) in desired.items():
            source = {
                "kind": "personal_assistant_note",
                "note_hash": note.get("sourceHash"),
                "note_version": note.get("sourceVersion"),
                "item": item,
            }
            current = active.get(memory_id)
            if current is None:
                history = self.repository.history(memory_id)
                if history:
                    current = self.repository.undo(memory_id)
                else:
                    self.repository.add(
                        content,
                        memory_id=memory_id,
                        memory_type=section.rstrip("s"),
                        trust=trust,
                        scope={
                            "kind": "profile",
                            "target": "personal_assistant",
                            "section": section,
                            "item_id": item["id"],
                        },
                        source=source,
                    )
                    counts["added"] += 1
                    continue
            if current["content"] != content or current.get("source") != source:
                self.repository.correct(
                    memory_id,
                    content,
                    trust=trust,
                    source=source,
                )
                counts["updated"] += 1

        return counts
