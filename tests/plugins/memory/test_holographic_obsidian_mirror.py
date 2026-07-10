"""Obsidian mirror (Phase 5): durable facts become editable vault notes,
off by default, mechanical high-volume facts excluded."""

import pytest

from plugins.memory.holographic import HolographicMemoryProvider


@pytest.fixture
def provider(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    p = HolographicMemoryProvider(config={
        "db_path": str(tmp_path / "m.db"), "hrr_dim": 64,
        "infer_facts": True, "mirror_to_obsidian": True, "profile": "film-maker",
    })
    p.initialize(session_id="s")
    p._vault = vault
    yield p
    p.shutdown()


def _note(vault):
    return (vault / "MAIN VULT" / "_System" / "Hermes Knowledge Graph"
            / "Memory" / "film-maker - Memory Facts.md")


def _facts():
    from agent.memory_extraction import InferredFact
    return [
        InferredFact("We are building the memory system.", "subject"),
        InferredFact("Chose SQLite over Neo4j for the fact store.", "decision"),
    ]


def test_mirror_writes_an_editable_note(provider, monkeypatch):
    monkeypatch.setattr("agent.memory_extraction.extract_inferred_facts", lambda m: _facts())
    provider.on_session_end([{"role": "user", "content": "keep building"}])
    note = _note(provider._vault)
    assert note.exists()
    text = note.read_text()
    assert "type: memory" in text          # frontmatter
    assert "building the memory system" in text
    assert "Chose SQLite" in text
    assert "## Update -" in text           # dated append per WRITE_POLICY


def test_mirror_off_by_default(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    monkeypatch.setattr("agent.memory_extraction.extract_inferred_facts", lambda m: _facts())
    p = HolographicMemoryProvider(config={
        "db_path": str(tmp_path / "m.db"), "hrr_dim": 64, "infer_facts": True,
        # no mirror_to_obsidian -> off
    })
    p.initialize(session_id="s")
    p.on_session_end([{"role": "user", "content": "x"}])
    assert not (vault / "MAIN VULT").exists()  # vault untouched
    p.shutdown()


def test_high_volume_mechanical_facts_not_mirrored(provider, monkeypatch):
    from agent.memory_extraction import InferredFact
    # 'command'/'workspace' are mechanical categories — excluded from the note.
    monkeypatch.setattr("agent.memory_extraction.extract_inferred_facts",
                        lambda m: [InferredFact("Ran: codex exec --cd /x", "command")])
    provider.on_session_end([{"role": "user", "content": "x"}])
    note = _note(provider._vault)
    # note may be created (header) but the command line must not be in it
    if note.exists():
        assert "codex exec" not in note.read_text()
