from types import SimpleNamespace


def _stub_context(monkeypatch, server):
    monkeypatch.setattr(server, "_personal_assistant_runtime_context", lambda: {})
    monkeypatch.setattr(server, "_current_profile_name", lambda: "office-work")


def test_owner_home_resolution_fails_closed_from_another_profile(monkeypatch, tmp_path):
    import tui_gateway.server as server

    active_home = tmp_path / "default"
    active_home.mkdir()
    monkeypatch.setattr(server, "_current_profile_name", lambda: "default")
    monkeypatch.setattr(server, "_profile_home", lambda profile: None)
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: active_home)

    response = server._methods["personal_assistant.state.get"](
        "r1", {"profile": "office-work"}
    )

    assert response["error"]["code"] == 4007
    assert "office-work" in response["error"]["message"]
    assert not (active_home / "state" / "personal-assistant" / "home.json").exists()


def test_home_from_another_profile_keeps_owner_on_session_calls(monkeypatch, tmp_path):
    import tui_gateway.server as server
    from agent.personal_assistant_state import PersonalAssistantStateStore

    monkeypatch.setattr(server, "_current_profile_name", lambda: "default")
    monkeypatch.setattr(server, "_profile_home", lambda profile: None)
    monkeypatch.setattr(server, "_current_profile_name", lambda: "office-work")
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: tmp_path)
    PersonalAssistantStateStore(tmp_path).set_canonical_session("assistant-home")
    resumes = []
    monkeypatch.setitem(
        server._methods,
        "session.resume",
        lambda rid, params: resumes.append(params)
        or server._ok(rid, {"session_id": "assistant-live"}),
    )

    response = server._methods["personal_assistant.home"](
        "r1", {"profile": "office-work"}
    )

    assert response["result"]["canonical_session_id"] == "assistant-home"
    assert resumes[0]["profile"] == "office-work"


def test_starts_append_episodes_to_one_canonical_session(monkeypatch, tmp_path):
    import tui_gateway.server as server

    _stub_context(monkeypatch, server)
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    creates = []
    resumes = []
    submits = []
    monkeypatch.setitem(
        server._methods,
        "session.create",
        lambda rid, params: creates.append(params)
        or server._ok(
            rid,
            {"session_id": "live-created-1", "stored_session_id": "canonical-1"},
        ),
    )
    monkeypatch.setitem(
        server._methods,
        "session.resume",
        lambda rid, params: resumes.append(params)
        or server._ok(rid, {"session_id": "live-1", "resumed": "canonical-1"}),
    )
    monkeypatch.setitem(
        server._methods,
        "prompt.submit",
        lambda rid, params: submits.append(params)
        or server._ok(rid, {"status": "streaming"}),
    )

    first = server._methods["personal_assistant.start"](
        "r1", {"trigger": "manual", "userIntent": "plan launch"}
    )
    second = server._methods["personal_assistant.start"](
        "r2", {"trigger": "review", "userIntent": "review launch"}
    )

    assert first["result"]["canonical_session_id"] == "canonical-1"
    assert second["result"]["canonical_session_id"] == "canonical-1"
    assert len(creates) == 1
    assert resumes[0]["session_id"] == "canonical-1"
    assert [item["session_id"] for item in submits] == ["live-created-1", "live-1"]
    state = server._methods["personal_assistant.state.get"]("r3", {})["result"]["state"]
    assert [item["trigger"] for item in state["episodes"]] == ["manual", "review"]


def test_start_reinjects_durable_preferences_after_canonical_resume(monkeypatch, tmp_path):
    import tui_gateway.server as server
    from agent.personal_assistant_state import PersonalAssistantStateStore

    _stub_context(monkeypatch, server)
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    store = PersonalAssistantStateStore(tmp_path)
    state = store.read()
    store.patch(
        "edit",
        {},
        expected_version=state["version"],
        operations=[
            {
                "op": "upsert",
                "section": "preferences",
                "id": "task-shape",
                "value": {"title": "להגיש משימות כ־10+2"},
            }
        ],
    )
    store.set_canonical_session("assistant-home")
    monkeypatch.setitem(
        server._methods,
        "session.resume",
        lambda rid, params: server._ok(
            rid,
            {
                "session_id": "assistant-live",
                "session_key": "assistant-home",
                "message_count": 4,
            },
        ),
    )
    submitted = []
    monkeypatch.setitem(
        server._methods,
        "prompt.submit",
        lambda rid, params: submitted.append(params)
        or server._ok(rid, {"status": "streaming"}),
    )

    response = server._methods["personal_assistant.start"](
        "r1", {"trigger": "manual", "userIntent": "continue"}
    )

    assert response["result"]["status"] == "launched"
    assert '"preferences": [{"id": "task-shape", "title": "להגיש משימות כ־10+2"}]' in submitted[0]["text"]


def test_personal_assistant_prompt_sets_a_fast_foreground_contract():
    import tui_gateway.server as server

    prompt = server._personal_assistant_prompt("manual", "plan today", {})

    assert "one focused batch" in prompt
    assert "two foreground tool batches" in prompt
    assert "Do not search code, files, skills, or prior sessions" in prompt
    assert "Return a useful visible response" in prompt


def test_personal_assistant_runtime_policy_caps_only_its_agent():
    import tui_gateway.server as server

    agent = SimpleNamespace(max_iterations=60, ephemeral_system_prompt="Existing profile guidance")
    session = {"agent": agent}
    ordinary = SimpleNamespace(max_iterations=60, ephemeral_system_prompt="Ordinary guidance")
    with server._sessions_lock:
        server._sessions["policy-assistant"] = session
        server._sessions["policy-ordinary"] = {"agent": ordinary}
    try:
        server._apply_personal_assistant_runtime_policy_for_session("policy-assistant")
        server._apply_personal_assistant_runtime_policy_for_session("policy-assistant")
    finally:
        with server._sessions_lock:
            server._sessions.pop("policy-assistant", None)
            server._sessions.pop("policy-ordinary", None)

    assert session["personal_assistant"] is True
    assert agent.max_iterations == server._PERSONAL_ASSISTANT_MAX_ITERATIONS
    assert agent._foreground_tool_batch_limit == 2
    assert agent._tool_result_budget_override.default_result_size == 20_000
    assert agent._tool_result_budget_override.turn_budget == 40_000
    assert agent._force_codex_ttfb_watchdog is True
    assert agent._codex_ttfb_timeout_seconds == 60
    assert agent._single_attempt_silent_timeout is True
    assert agent.compression_in_place is True
    assert agent.ephemeral_system_prompt.count(server._PERSONAL_ASSISTANT_RESPONSIVENESS_POLICY) == 1

    assert ordinary.max_iterations == 60
    assert not hasattr(ordinary, "_tool_result_budget_override")


def test_explicit_create_marks_personal_assistant_for_deferred_policy(monkeypatch, tmp_path):
    import tui_gateway.server as server

    monkeypatch.setattr(server, "_schedule_agent_build", lambda sid: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_claim_active_session_slot", lambda *args, **kwargs: (None, None))
    monkeypatch.setattr(server, "_completion_cwd", lambda params=None: str(tmp_path))

    response = server._methods["session.create"](
        "r1",
        {
            "title": "Personal assistant",
            "source": "desktop",
            "personal_assistant": True,
        },
    )
    sid = response["result"]["session_id"]
    try:
        assert server._sessions[sid]["personal_assistant"] is True
    finally:
        with server._sessions_lock:
            server._sessions.pop(sid, None)


def test_explicit_cold_resume_marks_personal_assistant_for_deferred_policy(
    monkeypatch, tmp_path
):
    import tui_gateway.server as server

    class FakeDb:
        def get_session(self, session_id):
            return {"id": session_id, "title": "Personal assistant", "created_at": 1.0}

        def get_session_by_title(self, title):
            return None

        def resolve_resume_session_id(self, session_id):
            return session_id

        def reopen_session(self, session_id):
            return None

        def get_messages_as_conversation(self, session_id, include_ancestors=False):
            return []

    monkeypatch.setattr(server, "_get_db", lambda: FakeDb())
    monkeypatch.setattr(server, "_schedule_agent_build", lambda sid: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_claim_active_session_slot", lambda *args, **kwargs: (None, None))
    monkeypatch.setattr(server, "_default_session_cwd", lambda: str(tmp_path))

    response = server._methods["session.resume"](
        "r1",
        {
            "session_id": "legacy-assistant",
            "source": "desktop",
            "personal_assistant": True,
        },
    )
    sid = response["result"]["session_id"]
    try:
        assert server._sessions[sid]["personal_assistant"] is True
    finally:
        with server._sessions_lock:
            server._sessions.pop(sid, None)


def test_canonical_cold_resume_restores_personal_assistant_policy_without_ui_flag(
    monkeypatch, tmp_path
):
    import tui_gateway.server as server
    from agent.personal_assistant_state import PersonalAssistantStateStore

    class FakeDb:
        def get_session(self, session_id):
            return {"id": session_id, "title": "Personal assistant", "created_at": 1.0}

        def get_session_by_title(self, title):
            return None

        def resolve_resume_session_id(self, session_id):
            return (
                "canonical-assistant-continuation"
                if session_id == "canonical-assistant"
                else session_id
            )

        def reopen_session(self, session_id):
            return None

        def get_messages_as_conversation(self, session_id, include_ancestors=False):
            return []

    PersonalAssistantStateStore(tmp_path).set_canonical_session("canonical-assistant")
    monkeypatch.setattr(server, "_profile_home", lambda profile: None)
    monkeypatch.setattr(server, "_current_profile_name", lambda: "office-work")
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(server, "_get_db", lambda: FakeDb())
    monkeypatch.setattr(server, "_schedule_agent_build", lambda sid: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_claim_active_session_slot", lambda *args, **kwargs: (None, None))
    monkeypatch.setattr(server, "_default_session_cwd", lambda: str(tmp_path))

    response = server._methods["session.resume"](
        "r1",
        {
            "session_id": "canonical-assistant-continuation",
            "source": "desktop",
        },
    )
    sid = response["result"]["session_id"]
    try:
        assert server._sessions[sid]["personal_assistant"] is True
    finally:
        with server._sessions_lock:
            server._sessions.pop(sid, None)


def test_user_chat_named_personal_assistant_does_not_receive_assistant_policy(
    monkeypatch, tmp_path
):
    import tui_gateway.server as server

    monkeypatch.setattr(server, "_schedule_agent_build", lambda sid: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_claim_active_session_slot", lambda *args, **kwargs: (None, None))
    monkeypatch.setattr(server, "_completion_cwd", lambda params=None: str(tmp_path))

    response = server._methods["session.create"](
        "r1", {"title": "Personal assistant", "source": "desktop"}
    )
    sid = response["result"]["session_id"]
    try:
        assert server._sessions[sid]["personal_assistant"] is False
    finally:
        with server._sessions_lock:
            server._sessions.pop(sid, None)


def test_start_applies_runtime_policy_before_submitting(monkeypatch, tmp_path):
    import tui_gateway.server as server

    _stub_context(monkeypatch, server)
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    monkeypatch.setitem(
        server._methods,
        "session.create",
        lambda rid, params: server._ok(
            rid, {"session_id": "assistant-live", "stored_session_id": "assistant-home"}
        ),
    )
    applied = []
    submitted = []
    monkeypatch.setattr(
        server,
        "_apply_personal_assistant_runtime_policy_for_session",
        lambda sid: applied.append(sid),
    )
    monkeypatch.setitem(
        server._methods,
        "prompt.submit",
        lambda rid, params: submitted.append(params) or server._ok(rid, {"status": "streaming"}),
    )

    response = server._methods["personal_assistant.start"](
        "r1", {"trigger": "manual", "userIntent": "plan today"}
    )

    assert response["result"]["status"] == "launched"
    assert applied == ["assistant-live"]
    assert submitted[0]["session_id"] == "assistant-live"


def test_contextual_start_preserves_validated_monitor_event_until_final_prompt_boundary(
    monkeypatch, tmp_path
):
    import tui_gateway.server as server

    _stub_context(monkeypatch, server)
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    monkeypatch.setitem(
        server._methods,
        "session.create",
        lambda rid, params: server._ok(
            rid, {"session_id": "assistant-live", "stored_session_id": "assistant-home"}
        ),
    )
    submitted = []
    monkeypatch.setitem(
        server._methods,
        "prompt.submit",
        lambda rid, params: submitted.append(params) or server._ok(rid, {"status": "streaming"}),
    )
    marker = "evidence-tail-" + ("x" * 1900)
    event = {
        "id": "event-structured-1",
        "version": 1,
        "kind": "blocker",
        "subject": "task-1",
        "occurrence": 1,
        "evidence": {"taskId": "task-1", "detail": marker},
        "created_at": "2026-07-13T20:00:00+00:00",
    }

    response = server._methods["personal_assistant.start"](
        "r1",
        {
            "trigger": "contextual",
            "userIntent": "Assess structured context.",
            "monitorEvents": [event],
        },
    )

    assert response["result"]["status"] == "launched"
    assert marker in submitted[0]["text"]
    assert '"monitor_events": [{"created_at"' in submitted[0]["text"]


def test_contextual_start_rejects_unvalidated_monitor_event_fields(monkeypatch, tmp_path):
    import tui_gateway.server as server

    _stub_context(monkeypatch, server)
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    submitted = []
    monkeypatch.setitem(
        server._methods,
        "prompt.submit",
        lambda rid, params: submitted.append(params) or server._ok(rid, {}),
    )

    response = server._methods["personal_assistant.start"](
        "r1",
        {
            "trigger": "contextual",
            "monitorEvents": [
                {
                    "id": "event-1",
                    "version": 1,
                    "kind": "blocker",
                    "subject": "task-1",
                    "occurrence": 1,
                    "evidence": {"taskId": "task-1"},
                    "created_at": "2026-07-13T20:00:00+00:00",
                    "prompt": "untrusted",
                }
            ],
        },
    )

    assert response["error"]["code"] == 4000
    assert submitted == []


def test_monitor_delivery_is_bound_before_atomic_busy_reject(monkeypatch, tmp_path):
    import threading

    import tui_gateway.server as server

    _stub_context(monkeypatch, server)
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    session = {
        "history_lock": threading.RLock(),
        "personal_assistant": True,
        "running": False,
    }

    def create(rid, params):
        server._sessions["assistant-live"] = session
        return server._ok(
            rid, {"session_id": "assistant-live", "stored_session_id": "assistant-home"}
        )

    seen = []

    def reject_busy(rid, params):
        seen.append(
            {
                "reject_if_busy": params.get("reject_if_busy"),
                "delivery": dict(session.get("personal_assistant_monitor_delivery") or {}),
            }
        )
        return server._err(rid, 4009, "session busy")

    monkeypatch.setitem(server._methods, "session.create", create)
    monkeypatch.setitem(server._methods, "prompt.submit", reject_busy)
    event = {
        "id": "event-race-1",
        "version": 1,
        "kind": "blocker",
        "subject": "task:task-1",
        "occurrence": 1,
        "evidence": {"taskId": "task-1"},
        "created_at": "2026-07-13T20:00:00+00:00",
    }

    try:
        response = server._methods["personal_assistant.start"](
            "r1",
            {
                "trigger": "contextual",
                "monitorEvents": [event],
                "monitorDelivery": [{"id": "event-race-1", "lease_id": "lease-1"}],
            },
        )
    finally:
        server._sessions.pop("assistant-live", None)

    assert response["error"]["code"] == 4009
    assert seen[0]["reject_if_busy"] is True
    assert seen[0]["delivery"]["events"] == [
        {"id": "event-race-1", "lease_id": "lease-1"}
    ]
    assert "personal_assistant_monitor_delivery" not in session


def test_internal_monitor_submit_rejects_busy_session_without_interrupt_or_queue(monkeypatch):
    import threading

    import tui_gateway.server as server

    session = {
        "history_lock": threading.RLock(),
        "personal_assistant": True,
        "running": True,
        "transport": None,
    }
    monkeypatch.setattr(
        server,
        "_handle_busy_submit",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not interrupt")),
    )
    server._sessions["assistant-race"] = session
    try:
        response = server._methods["prompt.submit"](
            "r1",
            {
                "session_id": "assistant-race",
                "text": "monitor context",
                "reject_if_busy": True,
            },
        )
    finally:
        server._sessions.pop("assistant-race", None)

    assert response["error"]["code"] == 4009
    assert "queued_prompt" not in session


def test_stale_canonical_is_recreated_once_without_forgetting_state(monkeypatch, tmp_path):
    import tui_gateway.server as server
    from agent.personal_assistant_state import PersonalAssistantStateStore

    _stub_context(monkeypatch, server)
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    store = PersonalAssistantStateStore(tmp_path)
    store.patch("edit", {"outcomes": [{"id": "important", "title": "important work"}]})
    store.set_canonical_session("stale")
    creates = []
    submits = []
    monkeypatch.setitem(
        server._methods,
        "session.resume",
        lambda rid, params: server._err(rid, 4007, "session not found"),
    )
    monkeypatch.setitem(
        server._methods,
        "session.create",
        lambda rid, params: creates.append(params)
        or server._ok(
            rid,
            {"session_id": "replacement-live", "stored_session_id": "replacement"},
        ),
    )
    monkeypatch.setitem(
        server._methods,
        "prompt.submit",
        lambda rid, params: submits.append(params)
        or server._ok(rid, {"status": "streaming"}),
    )

    response = server._methods["personal_assistant.start"]("r1", {"trigger": "contextual"})

    assert response["result"]["canonical_session_id"] == "replacement"
    assert len(creates) == 1
    assert "important work" in submits[0]["text"]
    assert store.read()["outcomes"][0]["title"] == "important work"


def test_home_returns_live_and_canonical_session_ids(monkeypatch, tmp_path):
    import tui_gateway.server as server
    from agent.personal_assistant_state import PersonalAssistantStateStore

    _stub_context(monkeypatch, server)
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    PersonalAssistantStateStore(tmp_path).set_canonical_session("assistant-home")
    monkeypatch.setitem(
        server._methods,
        "session.resume",
        lambda rid, params: server._ok(rid, {"session_id": "assistant-live"}),
    )
    applied = []
    monkeypatch.setattr(
        server,
        "_apply_personal_assistant_runtime_policy_for_session",
        lambda sid: applied.append(sid),
    )

    response = server._methods["personal_assistant.home"]("r1", {})

    assert response["result"]["session_id"] == "assistant-live"
    assert response["result"]["canonical_session_id"] == "assistant-home"
    assert response["result"]["state"]["sessionId"] == "assistant-home"
    assert applied == ["assistant-live"]


def test_home_preserves_unread_activity_until_the_transcript_is_read(monkeypatch, tmp_path):
    import tui_gateway.server as server
    from agent.personal_assistant_state import PersonalAssistantStateStore

    _stub_context(monkeypatch, server)
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    store = PersonalAssistantStateStore(tmp_path)
    store.set_canonical_session("assistant-home")
    store.patch("edit", {"unreadCount": 5})
    monkeypatch.setitem(
        server._methods,
        "session.resume",
        lambda rid, params: server._ok(rid, {"session_id": "assistant-live"}),
    )

    response = server._methods["personal_assistant.home"]("r1", {})

    assert response["result"]["state"]["unreadCount"] == 5
    assert store.public()["unreadCount"] == 5


def test_read_acknowledgement_is_idempotent(monkeypatch, tmp_path):
    import tui_gateway.server as server
    from agent.personal_assistant_state import PersonalAssistantStateStore

    _stub_context(monkeypatch, server)
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    store = PersonalAssistantStateStore(tmp_path)
    store.patch("edit", {"unreadCount": 3})

    first = server._methods["personal_assistant.read"](
        "r1", {"profile": "office-work"}
    )["result"]["state"]
    second = server._methods["personal_assistant.read"](
        "r2", {"profile": "office-work"}
    )["result"]["state"]

    assert first["unreadCount"] == 0
    assert second["unreadCount"] == 0
    assert second["version"] == first["version"]


def test_read_acknowledgement_retries_a_concurrent_state_change(monkeypatch, tmp_path):
    import tui_gateway.server as server
    from agent.personal_assistant_state import PersonalAssistantStateStore, StateVersionConflict

    _stub_context(monkeypatch, server)
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    store = PersonalAssistantStateStore(tmp_path)
    store.patch("edit", {"unreadCount": 1})
    original_patch = PersonalAssistantStateStore.patch
    attempts = 0

    def conflict_once(self, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            original_patch(self, "edit", {"unreadCount": 2})
            raise StateVersionConflict(self.read()["version"])
        return original_patch(self, *args, **kwargs)

    monkeypatch.setattr(PersonalAssistantStateStore, "patch", conflict_once)

    response = server._methods["personal_assistant.read"](
        "r1", {"profile": "office-work"}
    )

    assert response["result"]["state"]["unreadCount"] == 0
    assert attempts == 2


def test_start_idempotency_does_not_submit_twice(monkeypatch, tmp_path):
    import tui_gateway.server as server

    _stub_context(monkeypatch, server)
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    monkeypatch.setitem(
        server._methods, "session.create", lambda rid, params: server._ok(rid, {"session_id": "one"})
    )
    monkeypatch.setitem(
        server._methods, "session.resume", lambda rid, params: server._ok(rid, {"session_id": "one"})
    )
    submits = []
    monkeypatch.setitem(
        server._methods,
        "prompt.submit",
        lambda rid, params: submits.append(params) or server._ok(rid, {}),
    )

    first = server._methods["personal_assistant.start"](
        "r1", {"trigger": "manual", "idempotencyKey": "same"}
    )
    second = server._methods["personal_assistant.start"](
        "r2", {"trigger": "manual", "idempotencyKey": "same"}
    )

    assert first["result"]["status"] == "launched"
    assert second["result"]["status"] == "already_submitted"
    assert len(submits) == 1


def test_state_rpc_uses_camel_case_contract_and_returns_conflict_snapshot(monkeypatch, tmp_path):
    import tui_gateway.server as server

    _stub_context(monkeypatch, server)
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    initial = server._methods["personal_assistant.state.get"]("r1", {})["result"]["state"]
    changed = server._methods["personal_assistant.state.patch"](
        "r2",
        {
            "expectedVersion": initial["version"],
            "operations": [
                {"op": "upsert", "section": "outcomes", "id": "o1", "value": {"title": "Ship"}},
                {"op": "set", "section": "capacity", "value": {"summary": "4h", "updatedAt": "now"}},
            ],
        },
    )["result"]["state"]
    assert changed["schemaVersion"] == 1
    assert changed["outcomes"][0]["id"] == "o1"
    assert changed["capacity"]["summary"] == "4h"

    conflict = server._methods["personal_assistant.state.patch"](
        "r3", {"expectedVersion": initial["version"], "operations": [{"op": "forget", "section": "outcomes", "id": "o1"}]}
    )
    assert conflict["error"]["code"] == 4091
    assert conflict["error"]["data"]["state"]["version"] == changed["version"]
