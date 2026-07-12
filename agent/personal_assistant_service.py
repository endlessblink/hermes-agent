"""Shared cache and Obsidian transaction boundary for assistant state RPCs."""

from __future__ import annotations

import copy
from typing import Any

from agent.personal_assistant_obsidian import DURABLE_SECTIONS, PersonalAssistantObsidianAdapter
from agent.personal_assistant_state import PersonalAssistantStateStore, StateVersionConflict, _apply_operation


class PersonalAssistantStateService:
    def __init__(self, store: PersonalAssistantStateStore, adapter: PersonalAssistantObsidianAdapter):
        self.store = store
        self.adapter = adapter

    def get(self) -> dict[str, Any]:
        note = self.adapter.read()
        state = self.store.read()
        source = state.get("durableSource") or {}
        if note.get("sourceHash") != source.get("hash"):
            def reconcile(value: dict[str, Any]) -> None:
                for section in DURABLE_SECTIONS:
                    value[section] = copy.deepcopy(note.get(section) or [])
                value["durableSource"] = {
                    "kind": "obsidian",
                    "version": note.get("sourceVersion", 0),
                    "hash": note.get("sourceHash"),
                }
            state = self.store.update(reconcile)
        return state

    def patch(self, expected_version: int, operations: list[dict[str, Any]]) -> dict[str, Any]:
        state = self.get()
        if int(state.get("version") or 0) != expected_version:
            raise StateVersionConflict(int(state.get("version") or 0))
        durable_ops = [op for op in operations if op.get("section") in DURABLE_SECTIONS]
        note_result = None
        if durable_ops:
            note = self.adapter.read()
            proposed = {section: copy.deepcopy(note.get(section) or []) for section in DURABLE_SECTIONS}
            proposed["archived"] = copy.deepcopy(note.get("archived") or [])
            editable = set(DURABLE_SECTIONS)
            for operation in durable_ops:
                if operation.get("op") == "archive":
                    section = str(operation.get("section"))
                    item_id = str(operation.get("id") or "")
                    found = next((item for item in proposed[section] if item.get("id") == item_id), None)
                    if found:
                        proposed[section] = [item for item in proposed[section] if item.get("id") != item_id]
                        proposed["archived"].append({**found, "archivedFrom": section})
                    continue
                _apply_operation(proposed, operation, editable)
            note_result = self.adapter.write(proposed, expected_hash=note.get("sourceHash"))

        updated = self.store.patch(
            "edit", {}, expected_version=expected_version, operations=operations
        )
        if note_result is not None:
            def receipt(value: dict[str, Any]) -> None:
                for section in DURABLE_SECTIONS:
                    value[section] = copy.deepcopy(note_result.get(section) or [])
                value["durableSource"] = {
                    "kind": "obsidian",
                    "version": note_result.get("sourceVersion", 0),
                    "hash": note_result.get("sourceHash"),
                }
            updated = self.store.update(receipt)
        return updated
