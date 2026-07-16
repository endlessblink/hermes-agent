from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import os
from pathlib import Path
import sys
import time


def _store_class():
    import agent.personal_assistant_state as state_module

    repo_root = Path(__file__).resolve().parents[2]
    assert Path(state_module.__file__).resolve().is_relative_to(repo_root)
    return state_module.PersonalAssistantStateStore


def _claim_canonical_home(profile_home: str) -> str:
    PersonalAssistantStateStore = _store_class()
    store = PersonalAssistantStateStore(Path(profile_home))

    def resolve(current: str | None):
        if current:
            return current, current
        time.sleep(0.1)
        candidate = f"assistant-{os.getpid()}"
        return candidate, candidate

    _state, canonical, _changed = store.resolve_canonical_session(resolve)
    return canonical


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


def test_canonical_home_claim_is_atomic_across_processes(tmp_path):
    PersonalAssistantStateStore = _store_class()
    method = "fork" if "fork" in multiprocessing.get_all_start_methods() else "spawn"
    context = multiprocessing.get_context(method)

    with context.Pool(2) as pool:
        claimed = pool.map(_claim_canonical_home, [str(tmp_path), str(tmp_path)])

    assert claimed[0] == claimed[1]
    assert PersonalAssistantStateStore(tmp_path).read()["canonical_session_id"] == claimed[0]


def test_windows_lock_path_is_non_truncating_and_byte_locked(tmp_path, monkeypatch):
    import agent.personal_assistant_state as state_module

    lock_path = tmp_path / "assistant.lock"
    lock_path.write_bytes(b"existing-lock")
    calls = []

    class FakeMsvcrt:
        LK_LOCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(_fd, operation, size):
            calls.append((operation, size))

    monkeypatch.setitem(sys.modules, "msvcrt", FakeMsvcrt)
    monkeypatch.setattr(state_module.os, "name", "nt")

    with state_module._locked_file(lock_path):
        assert lock_path.read_bytes().startswith(b"existing-lock")

    assert calls == [(FakeMsvcrt.LK_LOCK, 1), (FakeMsvcrt.LK_UNLCK, 1)]
