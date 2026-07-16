def test_personal_assistant_home_reuses_one_canonical_session(monkeypatch, tmp_path):
    import tui_gateway.server as server

    monkeypatch.setattr(server, "_current_profile_name", lambda: "office-work")
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    created = []
    resumed = []
    monkeypatch.setitem(
        server._methods,
        "session.create",
        lambda rid, params: created.append(params)
        or server._ok(rid, {"session_id": "assistant-live", "stored_session_id": "assistant-home"}),
    )
    monkeypatch.setitem(
        server._methods,
        "session.resume",
        lambda rid, params: resumed.append(params)
        or server._ok(rid, {"session_id": "assistant-live-2", "resumed": "assistant-home"}),
    )

    first = server._methods["personal_assistant.home"]("r1", {"profile": "office-work"})
    second = server._methods["personal_assistant.home"]("r2", {"profile": "office-work"})

    assert first["result"]["canonical_session_id"] == "assistant-home"
    assert second["result"]["canonical_session_id"] == "assistant-home"
    assert len(created) == 1
    assert resumed[0]["profile"] == "office-work"


def test_personal_assistant_home_clears_unread_on_open(monkeypatch, tmp_path):
    from pathlib import Path

    import agent.personal_assistant_state as state_module
    import tui_gateway.server as server

    repo_root = Path(__file__).resolve().parents[2]
    assert Path(state_module.__file__).resolve().is_relative_to(repo_root)
    PersonalAssistantStateStore = state_module.PersonalAssistantStateStore
    monkeypatch.setattr(server, "_current_profile_name", lambda: "office-work")
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    store = PersonalAssistantStateStore(tmp_path)
    store.set_canonical_session("assistant-home")
    store.increment_unread()
    monkeypatch.setitem(
        server._methods,
        "session.resume",
        lambda rid, params: server._ok(rid, {"session_id": "assistant-live", "resumed": "assistant-home"}),
    )

    response = server._methods["personal_assistant.home"]("r1", {"profile": "office-work"})

    assert response["result"]["state"]["unreadCount"] == 0
    assert PersonalAssistantStateStore(tmp_path).read()["unreadCount"] == 0
