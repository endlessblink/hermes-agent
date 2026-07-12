from concurrent.futures import ThreadPoolExecutor


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
