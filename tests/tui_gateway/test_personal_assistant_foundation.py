def test_personal_assistant_home_reuses_one_canonical_session(monkeypatch, tmp_path):
    import tui_gateway.server as server

    assert server.DESKTOP_BACKEND_CONTRACT >= 4
    monkeypatch.setattr(server, "_current_profile_name", lambda: "office-work")
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    created = []
    resumed = []

    def create(params):
        created.append(params)
        server._sessions["assistant-live"] = {"session_key": "assistant-home"}
        return server._ok(
            "r1",
            {"session_id": "assistant-live", "stored_session_id": "assistant-home"},
        )

    monkeypatch.setattr(server, "_ensure_session_db_row", lambda _session: True)
    monkeypatch.setitem(
        server._methods,
        "session.create",
        lambda rid, params: create(params),
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
    server._sessions.clear()


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


def test_personal_assistant_home_does_not_claim_an_unpersisted_session(
    monkeypatch, tmp_path
):
    import agent.personal_assistant_state as state_module
    import tui_gateway.server as server

    monkeypatch.setattr(server, "_current_profile_name", lambda: "office-work")
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)

    def create(rid, _params):
        server._sessions["assistant-live"] = {"session_key": "assistant-home"}
        return server._ok(
            rid,
            {"session_id": "assistant-live", "stored_session_id": "assistant-home"},
        )

    closed = []
    monkeypatch.setitem(server._methods, "session.create", create)
    monkeypatch.setattr(server, "_ensure_session_db_row", lambda _session: False)
    monkeypatch.setattr(
        server,
        "_close_session_by_id",
        lambda sid, **_kwargs: closed.append(sid) or True,
    )

    response = server._methods["personal_assistant.home"](
        "r1", {"profile": "office-work"}
    )

    assert response["error"]["code"] == 5036
    assert state_module.PersonalAssistantStateStore(tmp_path).read()[
        "canonical_session_id"
    ] is None
    assert closed == ["assistant-live"]
    server._sessions.clear()


def test_real_home_row_survives_title_update_and_recreates_after_delete(
    monkeypatch, tmp_path
):
    import tui_gateway.server as server
    from agent.personal_assistant_state import PersonalAssistantStateStore
    from hermes_state import SessionDB

    owner_home = tmp_path / ".hermes" / "profiles" / "office-work"
    owner_home.mkdir(parents=True)
    monkeypatch.setattr(server, "_current_profile_name", lambda: "default")
    monkeypatch.setattr(
        server,
        "_profile_home",
        lambda profile: owner_home if profile == "office-work" else None,
    )
    monkeypatch.setattr(server, "_enable_gateway_prompts", lambda: None)
    monkeypatch.setattr(server, "_schedule_agent_build", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        server, "_schedule_session_cap_enforcement", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(server, "_completion_cwd", lambda _params=None: str(tmp_path))
    monkeypatch.setattr(server, "_resolve_model", lambda: "test-model")
    monkeypatch.setattr(
        server,
        "_claim_active_session_slot",
        lambda *_args, **_kwargs: (None, None),
    )
    server._sessions.clear()

    first = server._methods["personal_assistant.home"](
        "r1", {"profile": "office-work"}
    )["result"]
    original = first["canonical_session_id"]
    db = SessionDB(db_path=owner_home / "state.db")
    assert db.get_session(original)["title"] == "Personal assistant"
    assert db.set_session_title(original, "Renamed assistant") is True
    assert server._close_session_by_id(first["session_id"], end_reason="test") is True

    reopened = server._methods["personal_assistant.home"](
        "r2", {"profile": "office-work"}
    )["result"]
    assert reopened["canonical_session_id"] == original
    assert db.get_session(original)["title"] == "Renamed assistant"
    assert server._close_session_by_id(reopened["session_id"], end_reason="test") is True

    assert db.delete_session(original, sessions_dir=owner_home / "sessions") is True
    replacement = server._methods["personal_assistant.home"](
        "r3", {"profile": "office-work"}
    )["result"]
    assert replacement["canonical_session_id"] != original
    assert db.get_session(replacement["canonical_session_id"]) is not None
    assert PersonalAssistantStateStore(owner_home).read()["canonical_session_id"] == replacement[
        "canonical_session_id"
    ]
    assert not (tmp_path / ".hermes" / "state.db").exists()

    server._close_session_by_id(replacement["session_id"], end_reason="test")
    db.close()
    server._sessions.clear()


def test_home_rolls_back_owner_row_when_canonical_state_write_fails(
    monkeypatch, tmp_path
):
    import agent.personal_assistant_state as state_module
    import tui_gateway.server as server
    from hermes_state import SessionDB

    owner_home = tmp_path / ".hermes" / "profiles" / "office-work"
    owner_home.mkdir(parents=True)
    monkeypatch.setattr(server, "_current_profile_name", lambda: "default")
    monkeypatch.setattr(
        server,
        "_profile_home",
        lambda profile: owner_home if profile == "office-work" else None,
    )
    monkeypatch.setattr(server, "_enable_gateway_prompts", lambda: None)
    monkeypatch.setattr(server, "_schedule_agent_build", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        server, "_schedule_session_cap_enforcement", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(server, "_completion_cwd", lambda _params=None: str(tmp_path))
    monkeypatch.setattr(server, "_resolve_model", lambda: "test-model")
    monkeypatch.setattr(
        server,
        "_claim_active_session_slot",
        lambda *_args, **_kwargs: (None, None),
    )
    monkeypatch.setattr(
        state_module.PersonalAssistantStateStore,
        "_persist",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    server._sessions.clear()

    response = server._methods["personal_assistant.home"](
        "r1", {"profile": "office-work"}
    )

    assert response["error"]["code"] == 5036
    assert server._sessions == {}
    db = SessionDB(db_path=owner_home / "state.db")
    assert db.search_sessions(limit=10) == []
    db.close()
