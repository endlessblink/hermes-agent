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


def test_contextual_start_binds_structured_delivery_before_submit(
    monkeypatch, tmp_path
):
    import threading

    import tui_gateway.server as server

    monkeypatch.setattr(server, "_current_profile_name", lambda: "office-work")
    monkeypatch.setattr(server, "_profile_home", lambda _profile: tmp_path)
    session = {
        "session_key": "assistant-home",
        "history_lock": threading.RLock(),
        "running": False,
    }
    monkeypatch.setattr(server, "_sessions", {"assistant-live": session})
    monkeypatch.setattr(
        server,
        "_open_personal_assistant_home",
        lambda rid, _params, **_kwargs: server._ok(
            rid,
            {
                "session_id": "assistant-live",
                "canonical_session_id": "assistant-home",
            },
        ),
    )
    submitted = []

    def submit(rid, params):
        delivery = session.get("personal_assistant_monitor_delivery")
        assert delivery["events"] == [
            {"id": "event-1", "lease_id": "lease-1"}
        ]
        assert params["reject_if_busy"] is True
        assert "Validated structured FlowState monitor events" in params["text"]
        submitted.append(params)
        return server._ok(rid, {"status": "accepted"})

    monkeypatch.setitem(server._methods, "prompt.submit", submit)
    event = {
        "id": "event-1",
        "version": 1,
        "kind": "deadline_risk",
        "subject": "task-pressure:overdue",
        "occurrence": 1,
        "evidence": {"overdue": 2},
        "created_at": "2026-07-16T12:00:00+00:00",
    }

    response = server._methods["personal_assistant.start"](
        "r1",
        {
            "profile": "office-work",
            "trigger": "contextual",
            "idempotencyKey": "monitor:event-1",
            "monitorEvents": [event],
            "monitorDelivery": [{"id": "event-1", "lease_id": "lease-1"}],
        },
    )

    assert response["result"]["status"] == "launched"
    assert submitted
    assert session["personal_assistant"] is True
    assert session["personal_assistant_profile_home"] == str(tmp_path)


def test_contextual_start_does_not_acknowledge_existing_unread_attention(
    monkeypatch, tmp_path
):
    import threading

    import tui_gateway.server as server
    from agent.personal_assistant_state import PersonalAssistantStateStore

    monkeypatch.setattr(server, "_current_profile_name", lambda: "office-work")
    monkeypatch.setattr(server, "_profile_home", lambda _profile: tmp_path)
    store = PersonalAssistantStateStore(tmp_path)
    store.set_canonical_session("assistant-home")
    store.increment_unread()
    store.increment_unread()
    session = {
        "session_key": "assistant-home",
        "history_lock": threading.RLock(),
        "running": False,
    }
    monkeypatch.setattr(server, "_sessions", {"assistant-live": session})
    monkeypatch.setitem(
        server._methods,
        "session.resume",
        lambda rid, _params: server._ok(
            rid, {"session_id": "assistant-live", "resumed": "assistant-home"}
        ),
    )
    monkeypatch.setitem(
        server._methods,
        "prompt.submit",
        lambda rid, _params: server._ok(rid, {"status": "accepted"}),
    )

    response = server._methods["personal_assistant.start"](
        "r1",
        {
            "profile": "office-work",
            "trigger": "contextual",
            "idempotencyKey": "monitor:event-unread",
            "monitorEvents": [
                {
                    "id": "event-unread",
                    "version": 1,
                    "kind": "deadline_risk",
                    "subject": "task-pressure:overdue",
                    "occurrence": 1,
                    "evidence": {"overdue": 2},
                    "created_at": "2026-07-16T12:00:00+00:00",
                }
            ],
            "monitorDelivery": [
                {"id": "event-unread", "lease_id": "lease-unread"}
            ],
        },
    )

    assert response["result"]["status"] == "launched"
    assert PersonalAssistantStateStore(tmp_path).read()["unreadCount"] == 2


def test_internal_monitor_submit_rejects_busy_without_interrupting(monkeypatch):
    import threading

    import tui_gateway.server as server

    session = {
        "history_lock": threading.RLock(),
        "personal_assistant": True,
        "running": True,
        "transport": None,
    }
    monkeypatch.setattr(server, "_sessions", {"assistant-live": session})
    monkeypatch.setattr(
        server,
        "_handle_busy_submit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("monitor submission must not interrupt")
        ),
    )

    response = server._methods["prompt.submit"](
        "r1",
        {
            "session_id": "assistant-live",
            "text": "monitor context",
            "reject_if_busy": True,
        },
    )

    assert response["error"]["code"] == 4009


def test_contextual_start_retries_a_duplicate_pending_episode(monkeypatch, tmp_path):
    import threading

    import tui_gateway.server as server
    from agent.personal_assistant_state import PersonalAssistantStateStore

    monkeypatch.setattr(server, "_current_profile_name", lambda: "office-work")
    monkeypatch.setattr(server, "_profile_home", lambda _profile: tmp_path)
    store = PersonalAssistantStateStore(tmp_path)
    store.append_episode(
        trigger="contextual",
        user_intent="retry",
        idempotency_key="monitor:event-retry",
    )
    session = {
        "session_key": "assistant-home",
        "history_lock": threading.RLock(),
        "running": False,
    }
    monkeypatch.setattr(server, "_sessions", {"assistant-live": session})
    monkeypatch.setattr(
        server,
        "_open_personal_assistant_home",
        lambda rid, _params, **_kwargs: server._ok(
            rid,
            {
                "session_id": "assistant-live",
                "canonical_session_id": "assistant-home",
            },
        ),
    )
    submitted = []
    monkeypatch.setitem(
        server._methods,
        "prompt.submit",
        lambda rid, params: submitted.append(params)
        or server._ok(rid, {"status": "accepted"}),
    )
    event = {
        "id": "event-retry",
        "version": 1,
        "kind": "deadline_risk",
        "subject": "task-pressure:overdue",
        "occurrence": 1,
        "evidence": {"overdue": 2},
        "created_at": "2026-07-16T12:00:00+00:00",
    }

    response = server._methods["personal_assistant.start"](
        "r1",
        {
            "profile": "office-work",
            "trigger": "contextual",
            "idempotencyKey": "monitor:event-retry",
            "monitorEvents": [event],
            "monitorDelivery": [
                {"id": "event-retry", "lease_id": "lease-retry"}
            ],
        },
    )

    assert response["result"]["status"] == "launched"
    assert len(submitted) == 1
    assert session["personal_assistant_monitor_delivery"]["events"] == [
        {"id": "event-retry", "lease_id": "lease-retry"}
    ]


def test_contextual_start_recovers_a_stale_processing_episode(
    monkeypatch, tmp_path
):
    import threading

    import tui_gateway.server as server
    from agent.personal_assistant_state import PersonalAssistantStateStore

    monkeypatch.setattr(server, "_current_profile_name", lambda: "office-work")
    monkeypatch.setattr(server, "_profile_home", lambda _profile: tmp_path)
    store = PersonalAssistantStateStore(tmp_path)
    _state, episode, _duplicate = store.append_episode(
        trigger="contextual",
        user_intent="recover",
        idempotency_key="monitor:event-stale",
    )
    store.mark_episode_status(episode["episode_id"], "processing")
    session = {
        "session_key": "assistant-home",
        "history_lock": threading.RLock(),
        "running": False,
    }
    monkeypatch.setattr(server, "_sessions", {"assistant-live": session})
    monkeypatch.setattr(
        server,
        "_open_personal_assistant_home",
        lambda rid, _params, **_kwargs: server._ok(
            rid,
            {
                "session_id": "assistant-live",
                "canonical_session_id": "assistant-home",
            },
        ),
    )
    submitted = []
    monkeypatch.setitem(
        server._methods,
        "prompt.submit",
        lambda rid, params: submitted.append(params)
        or server._ok(rid, {"status": "accepted"}),
    )
    event = {
        "id": "event-stale",
        "version": 1,
        "kind": "deadline_risk",
        "subject": "task-pressure:overdue",
        "occurrence": 1,
        "evidence": {"overdue": 2},
        "created_at": "2026-07-16T12:00:00+00:00",
    }

    response = server._methods["personal_assistant.start"](
        "r1",
        {
            "profile": "office-work",
            "trigger": "contextual",
            "idempotencyKey": "monitor:event-stale",
            "monitorEvents": [event],
            "monitorDelivery": [
                {"id": "event-stale", "lease_id": "lease-stale"}
            ],
        },
    )

    assert response["result"]["status"] == "launched"
    assert len(submitted) == 1


def test_contextual_start_never_overwrites_an_inflight_delivery(
    monkeypatch, tmp_path
):
    import threading

    import tui_gateway.server as server

    monkeypatch.setattr(server, "_current_profile_name", lambda: "office-work")
    monkeypatch.setattr(server, "_profile_home", lambda _profile: tmp_path)
    existing = {
        "profile_home": str(tmp_path),
        "episode_id": "episode-existing",
        "events": [{"id": "event-existing", "lease_id": "lease-existing"}],
    }
    session = {
        "session_key": "assistant-home",
        "history_lock": threading.RLock(),
        "running": False,
        "personal_assistant_monitor_delivery": existing,
    }
    monkeypatch.setattr(server, "_sessions", {"assistant-live": session})
    monkeypatch.setattr(
        server,
        "_open_personal_assistant_home",
        lambda rid, _params, **_kwargs: server._ok(
            rid,
            {
                "session_id": "assistant-live",
                "canonical_session_id": "assistant-home",
            },
        ),
    )
    monkeypatch.setitem(
        server._methods,
        "prompt.submit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a second monitor turn must not be submitted")
        ),
    )
    event = {
        "id": "event-new",
        "version": 1,
        "kind": "deadline_risk",
        "subject": "task-pressure:overdue",
        "occurrence": 2,
        "evidence": {"overdue": 3},
        "created_at": "2026-07-16T12:01:00+00:00",
    }

    response = server._methods["personal_assistant.start"](
        "r2",
        {
            "profile": "office-work",
            "trigger": "contextual",
            "idempotencyKey": "monitor:event-new",
            "monitorEvents": [event],
            "monitorDelivery": [
                {"id": "event-new", "lease_id": "lease-new"}
            ],
        },
    )

    assert response["error"]["code"] == 4009
    assert session["personal_assistant_monitor_delivery"] is existing


def test_monitor_delivery_retries_when_agent_initialization_fails(monkeypatch):
    import threading

    import tui_gateway.server as server

    threads = []

    class FakeThread:
        def __init__(self, target=None, daemon=None, **_kwargs):
            self.target = target
            threads.append(self)

        def start(self):
            return None

    session = {
        "session_key": "assistant-home",
        "history_lock": threading.RLock(),
        "history": [],
        "history_version": 0,
        "attached_images": [],
        "running": False,
        "agent": None,
        "personal_assistant": True,
        "personal_assistant_monitor_delivery": {"events": []},
    }
    monkeypatch.setattr(server, "_sessions", {"assistant-live": session})
    monkeypatch.setattr(server.threading, "Thread", FakeThread)
    monkeypatch.setattr(server, "_ensure_session_db_row", lambda _session: None)
    monkeypatch.setattr(server, "_persist_branch_seed", lambda _session: None)
    monkeypatch.setattr(server, "_start_agent_build", lambda *_args: None)
    monkeypatch.setattr(
        server,
        "_wait_agent",
        lambda *_args: server._err("r1", 5000, "agent build failed"),
    )
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: None)
    settled = []
    monkeypatch.setattr(
        server,
        "_finish_personal_assistant_monitor_delivery",
        lambda _session, **kwargs: settled.append(kwargs),
    )

    response = server._methods["prompt.submit"](
        "r1", {"session_id": "assistant-live", "text": "monitor context"}
    )
    threads[0].target()

    assert response["result"]["status"] == "streaming"
    assert settled == [
        {"status": "agent_initialization_failed", "has_visible_response": False}
    ]


def test_monitor_delivery_retries_when_cancelled_before_agent_is_ready(
    monkeypatch,
):
    import threading

    import tui_gateway.server as server

    threads = []

    class FakeThread:
        def __init__(self, target=None, daemon=None, **_kwargs):
            self.target = target
            threads.append(self)

        def start(self):
            return None

    session = {
        "session_key": "assistant-home",
        "history_lock": threading.RLock(),
        "history": [],
        "history_version": 0,
        "attached_images": [],
        "running": False,
        "agent": None,
        "personal_assistant": True,
        "personal_assistant_monitor_delivery": {"events": []},
    }
    monkeypatch.setattr(server, "_sessions", {"assistant-live": session})
    monkeypatch.setattr(server.threading, "Thread", FakeThread)
    monkeypatch.setattr(server, "_ensure_session_db_row", lambda _session: None)
    monkeypatch.setattr(server, "_persist_branch_seed", lambda _session: None)
    monkeypatch.setattr(server, "_start_agent_build", lambda *_args: None)
    monkeypatch.setattr(server, "_wait_agent", lambda *_args: None)
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_run_prompt_submit", lambda *_args: None)
    settled = []
    monkeypatch.setattr(
        server,
        "_finish_personal_assistant_monitor_delivery",
        lambda _session, **kwargs: settled.append(kwargs),
    )

    response = server._methods["prompt.submit"](
        "r1", {"session_id": "assistant-live", "text": "monitor context"}
    )
    session["_turn_cancel_requested"] = True
    threads[0].target()

    assert response["result"]["status"] == "streaming"
    assert settled == [{"status": "cancelled", "has_visible_response": False}]


def test_stdio_gateway_startup_starts_the_monitor_consumer(monkeypatch):
    import hermes_cli.config as config
    import tui_gateway.entry as entry

    calls = []
    monkeypatch.setattr(entry, "_install_sidecar_publisher", lambda: None)
    monkeypatch.setattr(
        entry.server,
        "start_personal_assistant_monitor_consumer",
        lambda: calls.append(True),
    )
    monkeypatch.setattr(config, "read_raw_config", lambda: {})
    monkeypatch.setattr(entry, "write_json", lambda _payload: True)
    monkeypatch.setattr(entry.sys, "stdin", [])
    monkeypatch.setattr(entry, "_log_exit", lambda _reason: None)

    entry.main()

    assert calls == [True]
