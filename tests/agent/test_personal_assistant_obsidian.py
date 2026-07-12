import pytest


def _adapter(tmp_path):
    from agent.personal_assistant_obsidian import PersonalAssistantObsidianAdapter

    root = tmp_path / "vault"
    workspace = root / "MAIN VULT"
    workspace.mkdir(parents=True)
    config = {"obsidian_vault": {"enabled": True, "canonical_vault_root": str(root), "visible_workspace": str(workspace), "allow_hidden_system_paths": True}}
    return PersonalAssistantObsidianAdapter(config)


def test_note_creation_edit_and_external_reconcile(tmp_path):
    from agent.personal_assistant_service import PersonalAssistantStateService
    from agent.personal_assistant_state import PersonalAssistantStateStore

    adapter = _adapter(tmp_path)
    service = PersonalAssistantStateService(PersonalAssistantStateStore(tmp_path / "profile"), adapter)
    initial = service.get()
    changed = service.patch(initial["version"], [{"op": "upsert", "section": "outcomes", "id": "o1", "value": {"title": "Ship proposal", "reason": "customer promise"}}])
    assert "[o1] Ship proposal" in adapter.path.read_text()
    assert changed["outcomes"][0]["reason"] == "customer promise"

    adapter.path.write_text(adapter.path.read_text().replace("Ship proposal", "Ship carefully"))
    reconciled = service.get()
    assert reconciled["outcomes"][0]["title"] == "Ship carefully"
    assert reconciled["durableSource"]["kind"] == "obsidian"


def test_archive_and_forget_roundtrip(tmp_path):
    from agent.personal_assistant_service import PersonalAssistantStateService
    from agent.personal_assistant_state import PersonalAssistantStateStore

    adapter = _adapter(tmp_path)
    service = PersonalAssistantStateService(PersonalAssistantStateStore(tmp_path / "profile"), adapter)
    state = service.get()
    state = service.patch(state["version"], [{"op": "upsert", "section": "preferences", "id": "p1", "value": {"title": "No meetings before ten"}}])
    state = service.patch(state["version"], [{"op": "archive", "section": "preferences", "id": "p1"}])
    assert "archivedFrom" in adapter.path.read_text()
    state = service.patch(state["version"], [{"op": "upsert", "section": "commitments", "id": "c1", "value": {"title": "Call Sam"}}])
    service.patch(state["version"], [{"op": "forget", "section": "commitments", "id": "c1"}])
    assert "Call Sam" not in adapter.path.read_text()


def test_malformed_note_and_conflict_do_not_claim_cache_update(tmp_path):
    from agent.personal_assistant_obsidian import PersonalAssistantNoteError
    from agent.personal_assistant_service import PersonalAssistantStateService
    from agent.personal_assistant_state import PersonalAssistantStateStore

    adapter = _adapter(tmp_path)
    store = PersonalAssistantStateStore(tmp_path / "profile")
    service = PersonalAssistantStateService(store, adapter)
    state = service.get()
    adapter.path.parent.mkdir(parents=True, exist_ok=True)
    adapter.path.write_text("# malformed\n")
    before = store.read()
    with pytest.raises(PersonalAssistantNoteError):
        service.patch(state["version"], [{"op": "upsert", "section": "outcomes", "id": "o1", "value": {"title": "Unsafe claim"}}])
    assert store.read() == before


def test_note_write_failure_leaves_cache_unchanged(tmp_path, monkeypatch):
    from agent.personal_assistant_obsidian import PersonalAssistantNoteError
    from agent.personal_assistant_service import PersonalAssistantStateService
    from agent.personal_assistant_state import PersonalAssistantStateStore

    adapter = _adapter(tmp_path)
    store = PersonalAssistantStateStore(tmp_path / "profile")
    service = PersonalAssistantStateService(store, adapter)
    state = service.get()
    before = store.read()
    monkeypatch.setattr(adapter, "write", lambda *args, **kwargs: (_ for _ in ()).throw(PersonalAssistantNoteError("conflict")))
    with pytest.raises(PersonalAssistantNoteError):
        service.patch(state["version"], [{"op": "upsert", "section": "preferences", "id": "p1", "value": {"title": "Deep work"}}])
    assert store.read() == before
