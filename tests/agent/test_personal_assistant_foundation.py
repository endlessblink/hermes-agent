from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def _store_class():
    import agent.personal_assistant_state as state_module

    repo_root = Path(__file__).resolve().parents[2]
    assert Path(state_module.__file__).resolve().is_relative_to(repo_root)
    return state_module.PersonalAssistantStateStore


def test_personal_assistant_state_is_durable_atomic_and_profile_scoped(tmp_path):
    PersonalAssistantStateStore = _store_class()
    store = PersonalAssistantStateStore(tmp_path)
    store.set_canonical_session("assistant-home")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(
            pool.map(
                lambda number: store.append_episode(
                    trigger="manual",
                    user_intent=f"request-{number}",
                    idempotency_key=str(number),
                ),
                range(8),
            )
        )

    reloaded = PersonalAssistantStateStore(tmp_path).read()
    assert reloaded["canonical_session_id"] == "assistant-home"
    assert len(reloaded["episode_summaries"]) == 8
    assert reloaded["version"] >= 9


def test_personal_assistant_read_acknowledgement_clears_unread(tmp_path):
    PersonalAssistantStateStore = _store_class()
    store = PersonalAssistantStateStore(tmp_path)
    store.increment_unread()
    store.increment_unread()

    acknowledged = store.mark_read()

    assert acknowledged["unreadCount"] == 0
    assert PersonalAssistantStateStore(tmp_path).read()["unreadCount"] == 0
