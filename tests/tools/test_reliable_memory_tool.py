from __future__ import annotations

import json

from agent.reliable_memory import ReliableMemoryRepository
from tools.memory_tool import MemoryStore, apply_memory_pending, memory_tool


def _store(tmp_path):
    repository = ReliableMemoryRepository(
        db_path=tmp_path / "memory.db",
        mirror_root=tmp_path / "vault",
    )
    return MemoryStore(reliable_repository=repository), repository


def test_legacy_add_routes_to_ledger_and_verified_obsidian_note(tmp_path):
    store, repository = _store(tmp_path)

    result = json.loads(
        memory_tool(
            action="add",
            target="user",
            content="The user prefers compact answers.",
            store=store,
        )
    )

    assert result["success"] is True
    assert result["memory"]["status"] == "active"
    assert repository.note_path(result["memory"]["id"]).is_file()


def test_search_and_why_return_source_receipts(tmp_path):
    store, _repository = _store(tmp_path)
    added = json.loads(
        memory_tool(
            action="add",
            target="memory",
            content="Hermes release checks require restart proof.",
            store=store,
        )
    )

    found = json.loads(
        memory_tool(
            action="search",
            target="memory",
            query="release restart",
            store=store,
        )
    )
    receipt = json.loads(
        memory_tool(
            action="why",
            target="memory",
            memory_id=added["memory"]["id"],
            store=store,
        )
    )

    assert found["memories"][0]["id"] == added["memory"]["id"]
    assert receipt["source"]["kind"] == "memory_tool"
    assert receipt["content_hash"]
    assert receipt["note_path"]


def test_memory_tool_claim_id_is_stable_and_conflicts_fail_closed(tmp_path):
    store, repository = _store(tmp_path)
    first = json.loads(memory_tool(
        action="add",
        target="user",
        content="The user prefers concise updates.",
        claim_id="user.preference.update-length",
        store=store,
    ))
    retry = json.loads(memory_tool(
        action="add",
        target="user",
        content="The user prefers concise updates.",
        claim_id="user.preference.update-length",
        store=store,
    ))
    conflicting = json.loads(memory_tool(
        action="add",
        target="user",
        content="The user prefers detailed updates.",
        claim_id="user.preference.update-length",
        store=store,
    ))

    assert retry["memory"]["id"] == first["memory"]["id"]
    assert conflicting["memory"]["conflict"] is True
    assert json.loads(memory_tool(
        action="search", target="user", query="updates", store=store
    ))["memories"] == []
    assert len(repository.history(first["memory"]["id"])) == 1
    conflicts = json.loads(memory_tool(
        action="conflicts", target="user", claim_id="user.preference.update-length", store=store
    ))
    assert len(conflicts["conflicts"]) == 2
    resolved = json.loads(memory_tool(
        action="resolve_conflict", target="user", claim_id="user.preference.update-length",
        memory_id=first["memory"]["id"], store=store
    ))
    assert resolved["memory"]["id"] == first["memory"]["id"]


def test_replace_remove_undo_and_purge_keep_mirror_in_sync(tmp_path):
    store, repository = _store(tmp_path)
    added = json.loads(
        memory_tool(
            action="add",
            target="memory",
            content="Planning starts at 09:00.",
            store=store,
        )
    )
    memory_id = added["memory"]["id"]

    replaced = json.loads(
        memory_tool(
            action="replace",
            target="memory",
            old_text=memory_id,
            content="Planning starts at 10:00.",
            store=store,
        )
    )
    removed = json.loads(
        memory_tool(
            action="remove",
            target="memory",
            old_text=memory_id,
            store=store,
        )
    )
    restored = json.loads(
        memory_tool(
            action="undo",
            target="memory",
            memory_id=memory_id,
            store=store,
        )
    )
    purged = json.loads(
        memory_tool(
            action="purge",
            target="memory",
            memory_id=memory_id,
            store=store,
        )
    )

    assert replaced["memory"]["revision"] == 2
    assert removed["memory"]["status"] == "hidden"
    assert restored["memory"]["status"] == "active"
    assert purged["purged"] is True
    assert not repository.note_path(memory_id).exists()


def test_reliable_write_honors_approval_gate_and_replays_after_approval(
    tmp_path, monkeypatch
):
    from tools import write_approval as wa

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.setattr(wa, "write_approval_enabled", lambda subsystem: True)
    store, repository = _store(tmp_path)

    staged = json.loads(
        memory_tool(
            action="add",
            target="memory",
            content="Require restart proof.",
            store=store,
        )
    )

    assert staged["staged"] is True
    assert repository.list_active() == []
    pending = wa.get_pending("memory", staged["pending_id"])

    applied = apply_memory_pending(pending["payload"], store)

    assert applied["success"] is True
    assert repository.list_active()[0]["content"] == "Require restart proof."


def test_reliable_batch_honors_approval_gate(tmp_path, monkeypatch):
    from tools import write_approval as wa

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.setattr(wa, "write_approval_enabled", lambda subsystem: True)
    store, repository = _store(tmp_path)

    staged = json.loads(
        memory_tool(
            target="memory",
            operations=[
                {"action": "add", "content": "First durable fact."},
                {"action": "add", "content": "Second durable fact."},
            ],
            store=store,
        )
    )

    assert staged["staged"] is True
    assert repository.list_active() == []
