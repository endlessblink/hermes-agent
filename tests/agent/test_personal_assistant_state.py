from concurrent.futures import ThreadPoolExecutor

import pytest


def test_state_store_is_durable_profile_scoped_and_atomic(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    store.patch("edit", {"working_picture": {"current_focus": "ship"}})
    store.set_canonical_session("assistant-home")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(
            pool.map(
                lambda number: store.append_episode(
                    trigger="manual", user_intent=str(number), idempotency_key=str(number)
                ),
                range(12),
            )
        )

    reloaded = PersonalAssistantStateStore(tmp_path).read()
    assert reloaded["schema_version"] == 1
    assert reloaded["canonical_session_id"] == "assistant-home"
    assert reloaded["working_picture"]["current_focus"] == "ship"
    assert len(reloaded["episode_summaries"]) == 12


def test_state_patch_archive_and_forget_episode(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    _, episode, _ = store.append_episode(trigger="review", user_intent="week")

    archived = store.patch("archive", {"episode_id": episode["episode_id"]})
    assert archived["episode_summaries"][0]["archived_at"]

    forgotten = store.patch("forget", {"episode_id": episode["episode_id"]})
    assert forgotten["episode_summaries"] == []


def test_episode_idempotency_returns_existing_episode(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    _, first, duplicate = store.append_episode(
        trigger="manual", user_intent="first", idempotency_key="request-1"
    )
    _, second, duplicate_second = store.append_episode(
        trigger="manual", user_intent="changed", idempotency_key="request-1"
    )

    assert duplicate is False
    assert duplicate_second is True
    assert second == first
    assert len(store.read()["episode_summaries"]) == 1


def test_public_operations_are_item_level_and_optimistically_versioned(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore, StateVersionConflict

    store = PersonalAssistantStateStore(tmp_path)
    initial = store.read()
    inspected = store.patch("inspect", {})
    assert inspected["version"] == initial["version"]

    changed = store.patch(
        "edit",
        {},
        expected_version=initial["version"],
        operations=[
            {"op": "upsert", "field": "outcomes", "id": "ship", "value": {"title": "Ship"}},
            {"op": "set", "field": "focus", "value": "ship"},
        ],
    )
    assert changed["outcomes"] == [{"id": "ship", "title": "Ship"}]
    assert changed["focus"] == "ship"
    assert "preferences" in store.public()

    try:
        store.patch("edit", {}, expected_version=initial["version"], operations=[])
    except StateVersionConflict as exc:
        assert exc.current_version == changed["version"]
    else:
        raise AssertionError("stale state update should conflict")


def _monitor_event(event_id="event-1", *, task_id="task-1", operation_id="op-1"):
    return {
        "id": event_id,
        "version": 1,
        "kind": "changed_high_priority",
        "subject": task_id,
        "occurrence": 1,
        "evidence": {
            "id": task_id,
            "title": "Ship the proposal",
            "operationId": operation_id,
        },
        "created_at": "2026-07-13T08:00:00+00:00",
    }


def test_monitor_context_ledger_merges_exact_events_idempotently_and_is_public(tmp_path):
    from agent.personal_assistant_state import (
        PersonalAssistantStateStore,
        validate_monitor_events,
    )

    store = PersonalAssistantStateStore(tmp_path)
    event = _monitor_event()
    validated = validate_monitor_events([event])
    assert validated == [event]
    assert validated[0] is not event

    first = store.merge_monitor_events(
        [event], disposition="merged", episode_id="episode-1"
    )
    repeated = store.merge_monitor_events(
        [event], disposition="merged", episode_id="episode-1"
    )

    assert len(repeated["context_ledger"]) == 1
    entry = repeated["context_ledger"][0]
    assert entry["event"] == event
    assert entry["event"] is not event
    assert entry["eventId"] == "event-1"
    assert entry["taskIds"] == ["task-1"]
    assert entry["operationIds"] == ["op-1"]
    assert entry["disposition"] == "merged"
    assert entry["episodeId"] == "episode-1"
    assert first["context_ledger"][0]["firstSeenAt"] == entry["firstSeenAt"]
    assert store.monitor_event_ids() == {"event-1"}
    assert store.monitor_event_ids(dispositions={"merged"}) == {"event-1"}
    assert store.has_monitor_event("event-1", dispositions={"merged"}) is True
    assert store.has_monitor_event("event-1", dispositions={"handled"}) is False
    assert store.public()["contextLedger"] == repeated["context_ledger"]


def test_monitor_context_ledger_marks_status_atomically_without_replacing_event(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    event = _monitor_event()
    store.merge_monitor_events([event], disposition="pending")

    changed = store.mark_monitor_events(
        ["event-1"], disposition="processing", episode_id="episode-2"
    )
    handled = store.mark_monitor_events(
        ["event-1"], disposition="handled", episode_id="episode-2"
    )

    assert changed["context_ledger"][0]["disposition"] == "processing"
    assert handled["context_ledger"][0]["disposition"] == "handled"
    assert handled["context_ledger"][0]["episodeId"] == "episode-2"
    assert handled["context_ledger"][0]["event"] == event
    with pytest.raises(ValueError, match="terminal"):
        store.mark_monitor_events(["event-1"], disposition="pending")
    with pytest.raises(ValueError, match="not found"):
        store.mark_monitor_events(["missing"], disposition="handled")


@pytest.mark.parametrize(
    "event, message",
    [
        ({"id": "missing-fields", "version": 1}, "fields"),
        ({**_monitor_event(), "version": 2}, "version"),
        ({**_monitor_event(), "unexpected": True}, "fields"),
        ({**_monitor_event(), "evidence": {"authToken": "secret"}}, "sensitive"),
        ({**_monitor_event(), "evidence": {"nested": {"too": {"deep": {"for": {"storage": 1}}}}}}, "depth"),
        ({**_monitor_event(), "evidence": {"title": "x" * 2001}}, "string"),
    ],
)
def test_monitor_context_ledger_rejects_malformed_or_unsafe_events(
    tmp_path, event, message
):
    from agent.personal_assistant_state import (
        PersonalAssistantStateStore,
        validate_monitor_events,
    )

    store = PersonalAssistantStateStore(tmp_path)

    with pytest.raises(ValueError, match=message):
        validate_monitor_events([event])
    with pytest.raises(ValueError, match=message):
        store.merge_monitor_events([event], disposition="pending")
    assert store.read()["context_ledger"] == []


def test_monitor_context_ledger_rejects_identity_collision(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    store.merge_monitor_events([_monitor_event()], disposition="pending")

    with pytest.raises(ValueError, match="collision"):
        store.merge_monitor_events(
            [_monitor_event(task_id="different-task")], disposition="pending"
        )


def test_monitor_context_ledger_is_bounded_and_preserves_active_entries(tmp_path):
    from agent.personal_assistant_state import (
        CONTEXT_LEDGER_LIMIT,
        PersonalAssistantStateStore,
    )

    store = PersonalAssistantStateStore(tmp_path)
    for index in range(CONTEXT_LEDGER_LIMIT):
        event_id = f"event-{index}"
        store.merge_monitor_events(
            [_monitor_event(event_id, task_id=f"task-{index}", operation_id=f"op-{index}")],
            disposition="pending",
        )

    with pytest.raises(ValueError, match="active entries"):
        store.merge_monitor_events(
            [_monitor_event("overflow", task_id="overflow", operation_id="overflow")],
            disposition="pending",
        )

    store.mark_monitor_events(["event-0"], disposition="handled")
    state = store.merge_monitor_events(
        [_monitor_event("replacement", task_id="replacement", operation_id="replacement")],
        disposition="pending",
    )
    ids = {entry["eventId"] for entry in state["context_ledger"]}
    assert len(ids) == CONTEXT_LEDGER_LIMIT
    assert "event-0" not in ids
    assert "replacement" in ids


def test_monitor_context_ledger_recovers_compatibly_from_legacy_or_invalid_state(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_text(
        '{"version": 4, "canonical_session_id": "assistant-home", "context_ledger": [{"bad": true}]}',
        encoding="utf-8",
    )

    state = store.read()

    assert state["canonical_session_id"] == "assistant-home"
    assert state["context_ledger"] == []
    assert store.public()["contextLedger"] == []


def test_protected_items_require_an_explicit_safe_disposition(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)

    with pytest.raises(ValueError, match="next action"):
        store.upsert_protected_item(
            {
                "id": "flowstate:health-blood-test",
                "source": "flowstate",
                "sourceId": "health-blood-test",
                "kind": "commitment",
                "title": "Arrange the required blood test",
                "consequence": "The surgery preparation can be delayed",
                "disposition": "actionable",
            }
        )

    state = store.upsert_protected_item(
        {
            "id": "flowstate:health-blood-test",
            "source": "flowstate",
            "sourceId": "health-blood-test",
            "kind": "commitment",
            "title": "Arrange the required blood test",
            "consequence": "The surgery preparation can be delayed",
            "disposition": "actionable",
            "nextAction": "Call the clinic and request a renewed referral",
            "sourceRevision": "42",
        }
    )

    assert state["protected_items"][0]["nextAction"].startswith("Call the clinic")
    assert store.public()["protectedItems"][0]["id"] == "flowstate:health-blood-test"


def test_protected_items_preserve_missing_context_as_a_visible_risk(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    state = store.upsert_protected_item(
        {
            "id": "notion:client-project",
            "source": "notion",
            "sourceId": "client-project",
            "kind": "project",
            "title": "Client delivery",
            "consequence": "Unknown",
            "disposition": "needs_context",
            "missingFields": ["deadline", "stakeholder"],
            "nextReviewAt": "2026-07-20T09:00:00+03:00",
        }
    )

    assert state["protected_items"][0]["missingFields"] == ["deadline", "stakeholder"]


def test_coverage_receipt_cannot_claim_all_clear_for_partial_or_unreviewed_scope(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    store.upsert_protected_item(
        {
            "id": "flowstate:health-blood-test",
            "source": "flowstate",
            "sourceId": "health-blood-test",
            "kind": "commitment",
            "title": "Arrange the required blood test",
            "consequence": "The surgery preparation can be delayed",
            "disposition": "actionable",
            "nextAction": "Call the clinic",
        }
    )

    receipt = store.record_coverage_receipt(
        cadence="daily",
        scope_fingerprint="flowstate:sequence-19",
        sources=[
            {"id": "flowstate", "status": "fresh", "revision": "19"},
            {"id": "calendar", "status": "partial", "revision": None},
        ],
        expected_item_ids=["flowstate:health-blood-test"],
        reviewed_item_ids=[],
        risk_item_ids=[],
        unresolved_item_ids=[],
    )

    assert receipt["complete"] is False
    assert receipt["allClear"] is False
    assert receipt["missingItemIds"] == ["flowstate:health-blood-test"]
    assert receipt["blockingReasons"] == [
        "source calendar is partial",
        "1 protected item was not reviewed",
    ]


def test_complete_coverage_receipt_stays_non_clear_while_a_risk_needs_attention(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    store.upsert_protected_item(
        {
            "id": "flowstate:health-blood-test",
            "source": "flowstate",
            "sourceId": "health-blood-test",
            "kind": "commitment",
            "title": "Arrange the required blood test",
            "consequence": "The surgery preparation can be delayed",
            "disposition": "actionable",
            "nextAction": "Call the clinic",
        }
    )

    receipt = store.record_coverage_receipt(
        cadence="weekly",
        scope_fingerprint="portfolio:complete-19",
        sources=[{"id": "flowstate", "status": "fresh", "revision": "19"}],
        expected_item_ids=["flowstate:health-blood-test"],
        reviewed_item_ids=["flowstate:health-blood-test"],
        risk_item_ids=["flowstate:health-blood-test"],
        unresolved_item_ids=[],
    )

    assert receipt["complete"] is True
    assert receipt["allClear"] is False
    assert store.public()["latestCoverageReceipt"]["cadence"] == "weekly"
