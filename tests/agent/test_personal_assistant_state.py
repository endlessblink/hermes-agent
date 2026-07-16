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


def test_assistant_mutation_provenance_is_durable_idempotent_and_exact(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    proof = {
        "operationId": "assistant-operation-1",
        "sessionKey": "assistant-session",
        "episodeId": "episode-1",
        "turnId": "turn-1",
        "tasks": [
            {"taskId": "task-1", "canonicalRevision": 7, "changeSequence": 41},
            {"taskId": "task-2", "canonicalRevision": 3, "changeSequence": 42},
        ],
    }

    first = store.record_assistant_mutation(proof)
    replay = PersonalAssistantStateStore(tmp_path).record_assistant_mutation({
        **proof,
        "episodeId": "episode-2",
        "turnId": "turn-2",
    })

    assert first["assistant_mutations"] == replay["assistant_mutations"]
    assert len(replay["assistant_mutations"]) == 1
    assert replay["assistant_mutations"][0]["operationId"] == "assistant-operation-1"
    assert replay["assistant_mutations"][0]["tasks"] == proof["tasks"]

    with pytest.raises(ValueError, match="identity collision"):
        store.record_assistant_mutation({
            **proof,
            "tasks": [{"taskId": "task-1", "canonicalRevision": 8, "changeSequence": 43}],
        })


def test_monitor_classifier_suppresses_exact_assistant_causes_and_revisions(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    store.record_assistant_mutation({
        "operationId": "assistant-operation-1",
        "sessionKey": "assistant-session",
        "episodeId": None,
        "turnId": "turn-1",
        "tasks": [{"taskId": "task-1", "canonicalRevision": 7, "changeSequence": 41}],
    })
    exact_operation = _monitor_event(operation_id="assistant-operation-1")
    exact_revision = _monitor_event("event-2", operation_id="external-op")
    exact_revision["evidence"].pop("operationId")
    exact_revision["evidence"]["canonicalRevision"] = 7
    conflicting_external = _monitor_event("event-3", operation_id="external-op")
    conflicting_external["evidence"]["canonicalRevision"] = 7
    newer_external = _monitor_event("event-4", operation_id="external-newer")
    newer_external["evidence"]["canonicalRevision"] = 8

    classified = store.classify_monitor_events(
        [exact_operation, exact_revision, conflicting_external, newer_external]
    )

    assert [event["id"] for event in classified["suppressed"]] == ["event-1", "event-2"]
    assert classified["merged"] == []
    assert [event["id"] for event in classified["remaining"]] == ["event-3", "event-4"]


def test_monitor_classifier_keeps_mixed_self_and_external_batch_visible(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    store.record_assistant_mutation({
        "operationId": "assistant-operation-1",
        "sessionKey": "assistant-session",
        "episodeId": None,
        "turnId": "turn-1",
        "tasks": [{"taskId": "task-1", "canonicalRevision": 7, "changeSequence": 41}],
    })
    mixed = _monitor_event("mixed", operation_id="assistant-operation-1")
    mixed["kind"] = "uncategorized_tasks"
    mixed["evidence"] = {
        "added": [
            {"taskId": "task-1", "operationId": "assistant-operation-1"},
            {"taskId": "task-2", "operationId": "external-operation-1"},
        ]
    }

    classified = store.classify_monitor_events([mixed])

    assert classified["suppressed"] == []
    assert classified["merged"] == []
    assert [event["id"] for event in classified["remaining"]] == ["mixed"]


def test_monitor_classifier_keeps_nullable_external_cause_in_mixed_batch_visible(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    store.record_assistant_mutation({
        "operationId": "assistant-operation-1",
        "sessionKey": "assistant-session",
        "episodeId": None,
        "turnId": "turn-1",
        "tasks": [{"taskId": "task-1", "canonicalRevision": 7, "changeSequence": 41}],
    })
    mixed = _monitor_event("mixed-null", operation_id="assistant-operation-1")
    mixed["kind"] = "uncategorized_tasks"
    mixed["evidence"] = {
        "added": [
            {"taskId": "task-1", "operationId": "assistant-operation-1"},
            {
                "taskId": "task-2",
                "causeSource": "legacy",
                "changeSequence": 42,
                "canonicalRevision": 3,
            },
        ]
    }

    classified = store.classify_monitor_events([mixed])

    assert classified["suppressed"] == []
    assert [event["id"] for event in classified["remaining"]] == ["mixed-null"]


def test_monitor_classifier_merges_only_active_context_overlap(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    active = _monitor_event("active", task_id="task-active", operation_id="old-active")
    handled = _monitor_event("handled", task_id="task-handled", operation_id="old-handled")
    store.merge_monitor_events([active], disposition="retry_wait")
    store.merge_monitor_events([handled], disposition="handled")

    active_update = _monitor_event("active-update", task_id="task-active", operation_id="external-a")
    handled_update = _monitor_event("handled-update", task_id="task-handled", operation_id="external-b")
    classified = store.classify_monitor_events([active_update, handled_update])

    assert [event["id"] for event in classified["merged"]] == ["active-update"]
    assert [event["id"] for event in classified["remaining"]] == ["handled-update"]


def test_monitor_classifier_does_not_remerge_later_external_change(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    prior = _monitor_event("prior", task_id="task-1", operation_id="external-old")
    store.merge_monitor_events([prior], disposition="merged")
    later = _monitor_event("later", task_id="task-1", operation_id="external-new")

    classified = store.classify_monitor_events([later])

    assert classified["merged"] == []
    assert [event["id"] for event in classified["remaining"]] == ["later"]


def test_monitor_classifier_keeps_mixed_active_and_external_batch_visible(tmp_path):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    store = PersonalAssistantStateStore(tmp_path)
    active = _monitor_event("active", task_id="task-1", operation_id="external-old")
    store.merge_monitor_events([active], disposition="retry_wait")
    mixed = _monitor_event("mixed-active", operation_id="external-new")
    mixed["kind"] = "uncategorized_tasks"
    mixed["evidence"] = {
        "added": [
            {"taskId": "task-1", "operationId": "external-new"},
            {"taskId": "task-2", "operationId": "external-other"},
        ]
    }

    classified = store.classify_monitor_events([mixed])

    assert classified["merged"] == []
    assert [event["id"] for event in classified["remaining"]] == ["mixed-active"]
