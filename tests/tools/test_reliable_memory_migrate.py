from __future__ import annotations

import json

from agent.reliable_memory import ReliableMemoryRepository
from tools.reliable_memory_migrate import (
    collect_legacy_memories,
    migrate_legacy_memories,
)


def _legacy_profile(tmp_path):
    memories = tmp_path / "memories"
    memories.mkdir()
    (memories / "MEMORY.md").write_text(
        "Hermes deploys require restart proof.\n§\nUse compact planning cards.",
        encoding="utf-8",
    )
    (memories / "USER.md").write_text(
        "The user prefers concise answers.", encoding="utf-8"
    )
    (memories / "SCOPED_MEMORY.jsonl").write_text(
        json.dumps(
            {
                "id": "workflow-hermes",
                "type": "workflow",
                "content": "Hermes changes need focused tests.",
                "entities": ["Hermes"],
                "project_paths": ["/workspace/hermes"],
                "sources": ["desktop"],
                "edges": [],
                "global": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    note = tmp_path / "Office Work Personal Assistant.md"
    note.write_text(
        """---
type: office-work-personal-assistant
schema_version: 1
source_version: 1
---

# Office Work Personal Assistant

## Outcomes

- [outcome-1] Ship reliable memory

## Commitments

- [commitment-1] Review the rollout <!-- hermes-meta {"owner":"user"} -->

## Preferences

- [preference-1] Ask one focused question

## Archived
""",
        encoding="utf-8",
    )
    return memories, note


def test_preview_is_read_only_and_stable(tmp_path):
    memories, note = _legacy_profile(tmp_path)
    before = {path: path.read_bytes() for path in [*memories.iterdir(), note]}

    first = collect_legacy_memories(memories_dir=memories, personal_assistant_note=note)
    second = collect_legacy_memories(memories_dir=memories, personal_assistant_note=note)

    assert first == second
    assert len(first) == 7
    assert {path: path.read_bytes() for path in before} == before
    assert not (tmp_path / "memory.db").exists()


def test_personal_assistant_identity_survives_title_edits(tmp_path):
    memories, note = _legacy_profile(tmp_path)
    before = collect_legacy_memories(
        memories_dir=memories,
        personal_assistant_note=note,
    )
    outcome_before = next(
        record
        for record in before
        if record["scope"].get("item_id") == "outcome-1"
    )
    note.write_text(
        note.read_text(encoding="utf-8").replace(
            "Ship reliable memory", "Ship verified memory"
        ),
        encoding="utf-8",
    )

    after = collect_legacy_memories(
        memories_dir=memories,
        personal_assistant_note=note,
    )
    outcome_after = next(
        record
        for record in after
        if record["scope"].get("item_id") == "outcome-1"
    )

    assert outcome_after["id"] == outcome_before["id"]
    assert outcome_after["content"] == "Ship verified memory"


def test_apply_is_idempotent_and_creates_backups_before_import(tmp_path):
    memories, note = _legacy_profile(tmp_path)
    repository = ReliableMemoryRepository(
        db_path=tmp_path / "memory.db",
        mirror_root=tmp_path / "vault",
    )
    backup_root = tmp_path / "backups"

    first = migrate_legacy_memories(
        repository=repository,
        memories_dir=memories,
        personal_assistant_note=note,
        backup_root=backup_root,
    )
    second = migrate_legacy_memories(
        repository=repository,
        memories_dir=memories,
        personal_assistant_note=note,
        backup_root=backup_root,
    )

    assert first["imported"] == 7
    assert second["imported"] == 0
    assert second["already_present"] == 7
    assert (backup_root / "MEMORY.md").is_file()
    assert (backup_root / "USER.md").is_file()
    assert (backup_root / "SCOPED_MEMORY.jsonl").is_file()
    assert (backup_root / "Office Work Personal Assistant.md").is_file()
    assert len(repository.list_active()) == 7
