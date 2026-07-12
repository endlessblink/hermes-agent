def _event():
    return {
        "id": "event-1",
        "lease_id": "lease-1",
        "kind": "deadline_risk",
        "evidence": {"overdue": 2},
    }


def test_monitor_retries_by_not_acking_failed_submission(monkeypatch, tmp_path):
    import tui_gateway.server as server

    acked = []
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.lease_candidate_event",
        lambda *args, **kwargs: _event(),
    )
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.ack_candidate_event",
        lambda *args: acked.append(args) or True,
    )
    monkeypatch.setitem(
        server._methods,
        "personal_assistant.start",
        lambda rid, params: server._err(rid, 5000, "submit failed"),
    )

    assert server._consume_personal_assistant_monitor_once(tmp_path) is False
    assert acked == []


def test_monitor_acks_accepted_idempotent_episode_and_emits_attention(monkeypatch, tmp_path):
    import tui_gateway.server as server

    calls = []
    acked = []
    emitted = []
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.lease_candidate_event",
        lambda *args, **kwargs: _event(),
    )
    monkeypatch.setattr(
        "agent.personal_assistant_monitor.ack_candidate_event",
        lambda *args: acked.append(args) or True,
    )
    monkeypatch.setitem(
        server._methods,
        "personal_assistant.start",
        lambda rid, params: calls.append(params)
        or server._ok(
            rid,
            {
                "status": "already_submitted",
                "session_id": "assistant-live",
                "episode": {"episode_id": "episode-1"},
            },
        ),
    )
    monkeypatch.setattr(
        server, "_emit", lambda event, sid, payload: emitted.append((event, sid, payload))
    )

    assert server._consume_personal_assistant_monitor_once(tmp_path) is True
    assert calls[0]["trigger"] == "contextual"
    assert calls[0]["idempotencyKey"] == "event-1"
    assert "deadline_risk" in calls[0]["userIntent"]
    assert acked[0][1:] == ("event-1", "lease-1")
    event, sid, payload = emitted[0]
    assert event == "personal_assistant.attention"
    assert sid == "assistant-live"
    assert payload == {
        "session_id": "assistant-live",
        "episode_id": "episode-1",
        "kind": "deadline_risk",
        "unread_count": 1,
        "pending_count": 0,
    }
