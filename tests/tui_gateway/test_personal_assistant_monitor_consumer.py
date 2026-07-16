def _event(index=1):
    return {
        "id": f"event-{index}",
        "version": 1,
        "lifecycle_version": 2,
        "lease_id": f"lease-{index}",
        "kind": "deadline_risk",
        "subject": "pressure:overdue",
        "occurrence": index,
        "evidence": {"overdue": index + 1},
        "created_at": "2026-07-13T20:00:00+00:00",
        "status": "leased",
        "attempts": 1,
        "lease": {"id": f"lease-{index}", "consumer": "tui-gateway"},
    }


def _canonical(event):
    return {
        key: event[key]
        for key in ("id", "version", "kind", "subject", "occurrence", "evidence", "created_at")
    }


def test_monitor_retries_failed_batch_with_safe_lifecycle(monkeypatch, tmp_path):
    import tui_gateway.server as server

    leased = iter([_event(), None])
    retried = []
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.lease_candidate_event",
        lambda *args, **kwargs: next(leased),
    )
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.retry_candidate_event",
        lambda *args, **kwargs: retried.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(server, "_sessions", {})
    monkeypatch.setitem(
        server._methods,
        "personal_assistant.start",
        lambda rid, params: server._err(rid, 5000, "submit failed"),
    )

    assert server._consume_personal_assistant_monitor_once(tmp_path) is False
    assert retried[0][0][1:3] == ("event-1", "lease-1")
    assert retried[0][0][3] == {"category": "episode_start_failed", "code": "5000"}


def test_monitor_defers_atomic_busy_race_without_consuming_retry(monkeypatch, tmp_path):
    import tui_gateway.server as server

    leased = iter([_event(), None])
    deferred = []
    retried = []
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.lease_candidate_event",
        lambda *args, **kwargs: next(leased),
    )
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.defer_candidate_event",
        lambda *args, **kwargs: deferred.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.retry_candidate_event",
        lambda *args, **kwargs: retried.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(server, "_sessions", {})
    monkeypatch.setitem(
        server._methods,
        "personal_assistant.start",
        lambda rid, params: server._err(rid, 4009, "session busy"),
    )

    assert server._consume_personal_assistant_monitor_once(tmp_path) is False
    assert deferred[0][0][1:3] == ("event-1", "lease-1")
    assert retried == []

    from agent.personal_assistant_state import PersonalAssistantStateStore

    assert PersonalAssistantStateStore(tmp_path).read()["context_ledger"][0]["disposition"] == "retry_wait"


def test_monitor_coalesces_idle_events_as_structured_batch_and_emits_attention(monkeypatch, tmp_path):
    import tui_gateway.server as server

    events = [_event(1), _event(2), _event(3)]
    leased = iter([*events, None])
    calls = []
    settled = []
    emitted = []
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.lease_candidate_event",
        lambda *args, **kwargs: next(leased),
    )
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.settle_candidate_event",
        lambda *args: settled.append(args) or True,
    )
    monkeypatch.setattr(server, "_sessions", {})

    def start(_rid, params):
        calls.append(params)
        server._sessions["assistant-live"] = {"personal_assistant": True, "running": True}
        return server._ok(
            _rid,
            {
                "status": "already_submitted",
                "session_id": "assistant-live",
                "episode": {"episode_id": "episode-1"},
            },
        )

    monkeypatch.setitem(
        server._methods,
        "personal_assistant.start",
        start,
    )
    monkeypatch.setattr(
        server, "_emit", lambda event, sid, payload: emitted.append((event, sid, payload))
    )

    assert server._consume_personal_assistant_monitor_once(tmp_path) is True
    assert calls[0]["trigger"] == "contextual"
    assert calls[0]["idempotencyKey"] == "monitor-batch:event-1,event-2,event-3"
    assert calls[0]["monitorEvents"] == [_canonical(event) for event in events]
    assert "Evidence:" not in calls[0]["userIntent"]
    assert settled == []
    from agent.personal_assistant_state import PersonalAssistantStateStore

    state = PersonalAssistantStateStore(tmp_path).read()
    assert [entry["disposition"] for entry in state["context_ledger"]] == [
        "processing", "processing", "processing",
    ]
    assert calls[0]["monitorDelivery"] == [
        {"id": "event-1", "lease_id": "lease-1"},
        {"id": "event-2", "lease_id": "lease-2"},
        {"id": "event-3", "lease_id": "lease-3"},
    ]
    event, sid, payload = emitted[0]
    assert event == "personal_assistant.attention"
    assert sid == "assistant-live"
    assert payload == {
        "session_id": "assistant-live",
        "episode_id": "episode-1",
        "kind": "context_batch",
        "unread_count": 1,
        "pending_count": 0,
    }


def test_monitor_merges_into_busy_assistant_without_starting_or_interrupting(monkeypatch, tmp_path):
    import tui_gateway.server as server

    leased = iter([_event(), None])
    settled = []
    deferred = []
    starts = []
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.lease_candidate_event",
        lambda *args, **kwargs: next(leased),
    )
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.settle_candidate_event",
        lambda *args: settled.append(args) or True,
    )
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.defer_candidate_event",
        lambda *args, **kwargs: deferred.append((args, kwargs)) or True,
    )
    active_session = {"personal_assistant": True, "running": True}
    monkeypatch.setattr(
        server,
        "_sessions",
        {"assistant-live": active_session},
    )
    monkeypatch.setitem(
        server._methods,
        "personal_assistant.start",
        lambda rid, params: starts.append(params) or server._err(rid, 5000, "must not start"),
    )

    assert server._consume_personal_assistant_monitor_once(tmp_path) is True
    assert starts == []
    assert settled == []

    from agent.personal_assistant_state import PersonalAssistantStateStore

    state = PersonalAssistantStateStore(tmp_path).read()
    assert state["context_ledger"][0]["eventId"] == "event-1"
    assert state["context_ledger"][0]["disposition"] == "retry_wait"
    assert deferred[0][0][1:3] == ("event-1", "lease-1")


def test_monitor_delivery_is_handled_only_after_visible_turn_completion(monkeypatch, tmp_path):
    import tui_gateway.server as server
    from agent.personal_assistant_state import PersonalAssistantStateStore

    event = _event()
    store = PersonalAssistantStateStore(tmp_path)
    store.merge_monitor_events([_canonical(event)], disposition="processing")
    settled = []
    retried = []
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.settle_candidate_event",
        lambda *args: settled.append(args) or True,
    )
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.retry_candidate_event",
        lambda *args, **kwargs: retried.append((args, kwargs)) or True,
    )
    session = {
        "personal_assistant_monitor_delivery": {
            "profile_home": str(tmp_path),
            "episode_id": None,
            "events": [{"id": "event-1", "lease_id": "lease-1"}],
        }
    }

    server._finish_personal_assistant_monitor_delivery(
        session,
        status="complete",
        has_visible_response=True,
    )

    assert retried == []
    assert settled[0][1:] == ("event-1", "lease-1", "handled")
    assert store.read()["context_ledger"][0]["disposition"] == "handled"
    assert "personal_assistant_monitor_delivery" not in session


def test_monitor_delivery_retries_when_contextual_turn_fails(monkeypatch, tmp_path):
    import tui_gateway.server as server
    from agent.personal_assistant_state import PersonalAssistantStateStore

    event = _event()
    store = PersonalAssistantStateStore(tmp_path)
    store.merge_monitor_events([_canonical(event)], disposition="processing")
    retried = []
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.retry_candidate_event",
        lambda *args, **kwargs: retried.append((args, kwargs)) or True,
    )
    session = {
        "personal_assistant_monitor_delivery": {
            "profile_home": str(tmp_path),
            "episode_id": None,
            "events": [{"id": "event-1", "lease_id": "lease-1"}],
        }
    }

    server._finish_personal_assistant_monitor_delivery(
        session,
        status="error",
        has_visible_response=False,
    )

    assert retried[0][0][1:3] == ("event-1", "lease-1")
    assert store.read()["context_ledger"][0]["disposition"] == "retry_wait"


def test_personal_assistant_tool_completion_records_only_verified_committed_receipts(
    monkeypatch, tmp_path
):
    import json
    import tui_gateway.server as server
    from agent.personal_assistant_state import PersonalAssistantStateStore

    session = {
        "personal_assistant": True,
        "session_key": "assistant-session",
        "personal_assistant_profile_home": str(tmp_path),
        "personal_assistant_episode_id": "episode-1",
        "watchdog_turn_id": "turn-1",
    }
    monkeypatch.setattr(server, "_sessions", {"assistant-live": session})
    committed = {
        "result": {
            "result": "committed",
            "receipt": {
                "ok": True,
                "status": "committed",
                "source": "local-api",
                "operationId": "assistant-operation-1",
                "affected": [
                    {
                        "entityType": "task",
                        "entityId": "task-1",
                        "canonicalRevision": 7,
                        "changeSequence": 41,
                    },
                    {
                        "entityType": "task",
                        "entityId": "task-2",
                        "canonicalRevision": 3,
                        "changeSequence": 42,
                    },
                ],
            },
        }
    }

    server._on_tool_complete(
        "assistant-live",
        "tool-1",
        "flowstate_update_task",
        {"operationId": "assistant-operation-1"},
        json.dumps(committed),
    )
    server._on_tool_complete(
        "assistant-live",
        "tool-2",
        "flowstate_update_task",
        {"operationId": "preview-operation"},
        json.dumps({"result": {"result": "preview", "operationId": "preview-operation"}}),
    )
    forged = json.loads(json.dumps(committed))
    forged["result"]["receipt"]["operationId"] = "forged-operation"
    server._on_tool_complete(
        "assistant-live",
        "tool-3",
        "flowstate_update_task",
        {"operationId": "different-operation"},
        json.dumps(forged),
    )
    wrong_source = json.loads(json.dumps(committed))
    wrong_source["result"]["receipt"]["source"] = "legacy"
    server._on_tool_complete(
        "assistant-live",
        "tool-4",
        "flowstate_update_task",
        {"operationId": "assistant-operation-1"},
        json.dumps(wrong_source),
    )

    mutations = PersonalAssistantStateStore(tmp_path).read()["assistant_mutations"]
    assert len(mutations) == 1
    assert mutations[0]["operationId"] == "assistant-operation-1"
    assert mutations[0]["sessionKey"] == "assistant-session"
    assert mutations[0]["episodeId"] == "episode-1"
    assert mutations[0]["turnId"] == "turn-1"
    assert [task["taskId"] for task in mutations[0]["tasks"]] == ["task-1", "task-2"]


def test_monitor_suppresses_self_origin_without_starting_an_episode(monkeypatch, tmp_path):
    import tui_gateway.server as server
    from agent.personal_assistant_state import PersonalAssistantStateStore

    event = _event()
    event["kind"] = "changed_high_priority"
    event["subject"] = "task:task-1"
    event["evidence"] = {
        "taskId": "task-1",
        "operationId": "assistant-operation-1",
        "canonicalRevision": 7,
    }
    leased = iter([event, None])
    settled = []
    starts = []
    store = PersonalAssistantStateStore(tmp_path)
    store.record_assistant_mutation({
        "operationId": "assistant-operation-1",
        "sessionKey": "assistant-session",
        "episodeId": "episode-1",
        "turnId": "turn-1",
        "tasks": [{"taskId": "task-1", "canonicalRevision": 7, "changeSequence": 41}],
    })
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.lease_candidate_event",
        lambda *args, **kwargs: next(leased),
    )
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.settle_candidate_event",
        lambda *args: settled.append(args) or True,
    )
    monkeypatch.setattr(server, "_sessions", {})
    monkeypatch.setitem(
        server._methods,
        "personal_assistant.start",
        lambda rid, params: starts.append(params) or server._err(rid, 5000, "must not start"),
    )

    assert server._consume_personal_assistant_monitor_once(tmp_path) is True
    assert starts == []
    assert settled[0][1:] == ("event-1", "lease-1", "suppressed")
    assert store.read()["context_ledger"][0]["disposition"] == "suppressed"


def test_busy_assistant_settles_self_origin_instead_of_deferring_it(monkeypatch, tmp_path):
    import tui_gateway.server as server
    from agent.personal_assistant_state import PersonalAssistantStateStore

    event = _event()
    event["kind"] = "changed_high_priority"
    event["subject"] = "task:task-1"
    event["evidence"] = {
        "taskId": "task-1",
        "operationId": "assistant-operation-1",
        "canonicalRevision": 7,
    }
    leased = iter([event, None])
    settled = []
    deferred = []
    store = PersonalAssistantStateStore(tmp_path)
    store.record_assistant_mutation({
        "operationId": "assistant-operation-1",
        "sessionKey": "assistant-session",
        "episodeId": "episode-1",
        "turnId": "turn-1",
        "tasks": [{"taskId": "task-1", "canonicalRevision": 7, "changeSequence": 41}],
    })
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.lease_candidate_event",
        lambda *args, **kwargs: next(leased),
    )
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.settle_candidate_event",
        lambda *args: settled.append(args) or True,
    )
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.defer_candidate_event",
        lambda *args, **kwargs: deferred.append(args) or True,
    )
    monkeypatch.setattr(
        server,
        "_sessions",
        {"assistant-live": {"personal_assistant": True, "running": True}},
    )

    assert server._consume_personal_assistant_monitor_once(tmp_path) is True
    assert settled[0][1:] == ("event-1", "lease-1", "suppressed")
    assert deferred == []


def test_monitor_merges_known_plan_overlap_but_launches_external_changes(monkeypatch, tmp_path):
    import tui_gateway.server as server
    from agent.personal_assistant_state import PersonalAssistantStateStore

    known = _event(1)
    known["kind"] = "changed_high_priority"
    known["subject"] = "task:task-known"
    known["evidence"] = {"taskId": "task-known", "operationId": "external-known"}
    external = _event(2)
    external["kind"] = "changed_high_priority"
    external["subject"] = "task:task-external"
    external["evidence"] = {"taskId": "task-external", "operationId": "external-new"}
    leased = iter([known, external, None])
    settled = []
    calls = []
    store = PersonalAssistantStateStore(tmp_path)
    prior = _canonical(known)
    prior["id"] = "prior-known"
    store.merge_monitor_events([prior], disposition="retry_wait")
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.lease_candidate_event",
        lambda *args, **kwargs: next(leased),
    )
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.settle_candidate_event",
        lambda *args: settled.append(args) or True,
    )
    monkeypatch.setattr(server, "_sessions", {})

    def start(rid, params):
        calls.append(params)
        server._sessions["assistant-live"] = {"personal_assistant": True, "running": True}
        return server._ok(rid, {
            "status": "already_submitted",
            "session_id": "assistant-live",
            "episode": {"episode_id": "episode-new"},
        })

    monkeypatch.setitem(server._methods, "personal_assistant.start", start)
    monkeypatch.setattr(server, "_emit", lambda *args, **kwargs: None)

    assert server._consume_personal_assistant_monitor_once(tmp_path) is True
    assert [event["id"] for event in calls[0]["monitorEvents"]] == ["event-2"]
    assert (tmp_path, "event-1", "lease-1", "merged") in settled
    dispositions = {
        entry["eventId"]: entry["disposition"]
        for entry in store.read()["context_ledger"]
    }
    assert dispositions["event-1"] == "merged"
    assert dispositions["event-2"] == "processing"
