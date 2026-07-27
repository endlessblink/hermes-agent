"""Dry-run-first import into the reliable memory ledger.

Legacy files remain untouched. Apply mode copies every source into a dedicated
backup directory before importing stable, idempotent records.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from agent.personal_assistant_memory import personal_assistant_memory_id
from agent.reliable_memory import ReliableMemoryRepository


_NAMESPACE = uuid.UUID("a64752fc-22a2-41e7-bdf1-e7dcb8a97313")
_DELIMITER = "\n§\n"


def _stable_id(target: str, source_key: str, content: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{target}\0{source_key}\0{content.strip()}"))


def _flat_entries(path: Path) -> list[str]:
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8")
    return [entry.strip() for entry in raw.split(_DELIMITER) if entry.strip()]


def _scoped_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("content"):
            entries.append(value)
    return entries


def _personal_assistant_entries(path: Path | None) -> list[tuple[str, dict[str, Any]]]:
    if path is None or not path.exists():
        return []
    from agent.personal_assistant_obsidian import (
        DURABLE_SECTIONS,
        PersonalAssistantObsidianAdapter,
    )

    parsed = PersonalAssistantObsidianAdapter._parse(path.read_text(encoding="utf-8"))
    values = []
    for section in DURABLE_SECTIONS:
        for item in parsed.get(section, []):
            if isinstance(item, dict) and item.get("id"):
                values.append((section, item))
    return values


def collect_legacy_memories(
    *,
    memories_dir: Path,
    personal_assistant_note: Path | None = None,
) -> list[dict[str, Any]]:
    """Return deterministic import records without creating or changing files."""
    records: list[dict[str, Any]] = []
    for filename, target, memory_type in (
        ("MEMORY.md", "memory", "environment_fact"),
        ("USER.md", "user", "user_preference"),
    ):
        for index, content in enumerate(_flat_entries(memories_dir / filename)):
            source_key = f"{filename}:{index}"
            records.append(
                {
                    "id": _stable_id(target, source_key, content),
                    "content": content,
                    "memory_type": memory_type,
                    "trust": "legacy_import",
                    "scope": {"kind": "global", "target": target},
                    "source": {
                        "kind": "legacy_file",
                        "path": str(memories_dir / filename),
                        "entry": index,
                    },
                }
            )

    scoped_path = memories_dir / "SCOPED_MEMORY.jsonl"
    for index, node in enumerate(_scoped_entries(scoped_path)):
        content = str(node["content"]).strip()
        node_id = str(node.get("id") or index)
        scope: dict[str, Any] = {
            "kind": "global" if node.get("global") else "scoped",
            "target": "scoped",
        }
        for key in ("entities", "project_paths", "sources", "edges"):
            if node.get(key):
                scope[key] = node[key]
        if node.get("project_paths"):
            scope["project"] = node["project_paths"][0]
        if node.get("sources"):
            scope["source"] = node["sources"][0]
        records.append(
            {
                "id": _stable_id("scoped", node_id, content),
                "content": content,
                "memory_type": str(node.get("type") or "environment_fact"),
                "trust": "legacy_import",
                "scope": scope,
                "source": {
                    "kind": "legacy_scoped_memory",
                    "path": str(scoped_path),
                    "node_id": node_id,
                },
            }
        )

    for section, item in _personal_assistant_entries(personal_assistant_note):
        content = str(item.get("title") or item.get("summary") or "").strip()
        item_id = str(item["id"])
        records.append(
            {
                "id": personal_assistant_memory_id(section, item_id),
                "content": content,
                "memory_type": {
                    "outcomes": "outcome",
                    "commitments": "commitment",
                    "preferences": "user_preference",
                }.get(section, "entity"),
                "trust": "legacy_import",
                "scope": {
                    "kind": "personal_assistant",
                    "target": "personal_assistant",
                    "section": section,
                    "item_id": item_id,
                },
                "source": {
                    "kind": "personal_assistant_note",
                    "path": str(personal_assistant_note),
                    "item": item,
                },
            }
        )
    return records


def migrate_legacy_memories(
    *,
    repository: ReliableMemoryRepository,
    memories_dir: Path,
    personal_assistant_note: Path | None,
    backup_root: Path,
) -> dict[str, Any]:
    records = collect_legacy_memories(
        memories_dir=memories_dir,
        personal_assistant_note=personal_assistant_note,
    )
    backup_root.mkdir(parents=True, exist_ok=True)
    sources = [
        memories_dir / "MEMORY.md",
        memories_dir / "USER.md",
        memories_dir / "SCOPED_MEMORY.jsonl",
    ]
    if personal_assistant_note is not None:
        sources.append(personal_assistant_note)
    backups = []
    for source in sources:
        if source.exists():
            destination = backup_root / source.name
            shutil.copy2(source, destination)
            backups.append(str(destination))

    imported = 0
    already_present = 0
    for record in records:
        if repository.history(record["id"]):
            already_present += 1
            continue
        repository.add(
            record["content"],
            memory_id=record["id"],
            memory_type=record["memory_type"],
            trust=record["trust"],
            scope=record["scope"],
            source=record["source"],
        )
        imported += 1
    return {
        "success": True,
        "imported": imported,
        "already_present": already_present,
        "total": len(records),
        "backups": backups,
        "sync": repository.sync_status(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Preview or import legacy Hermes memory into the reliable ledger."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create backups and import. Without this flag the command is read-only.",
    )
    args = parser.parse_args(argv)

    from agent.personal_assistant_obsidian import NOTE_PATH
    from agent.vault_knowledge.config import load_vault_config
    from hermes_constants import get_hermes_home
    from tools.memory_tool import get_memory_dir

    config = load_vault_config()
    pa_note = config.visible_workspace / NOTE_PATH
    records = collect_legacy_memories(
        memories_dir=get_memory_dir(),
        personal_assistant_note=pa_note,
    )
    if not args.apply:
        print(
            json.dumps(
                {
                    "success": True,
                    "dry_run": True,
                    "would_import": len(records),
                    "records": records,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    repository = ReliableMemoryRepository.from_profile()
    result = migrate_legacy_memories(
        repository=repository,
        memories_dir=get_memory_dir(),
        personal_assistant_note=pa_note,
        backup_root=get_hermes_home()
        / "backups"
        / f"reliable-memory-{int(time.time())}",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["success"] and result["sync"]["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
