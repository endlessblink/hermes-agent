import json
import threading
import time
from types import SimpleNamespace

import pytest

from agent.live_compaction_checkpoint import (
    LiveCompactionCheckpointStore,
    schedule_live_compaction_checkpoint,
)
from agent import conversation_compression


def _turn(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def test_checkpoint_apply_preserves_turns_appended_after_snapshot(tmp_path):
    store = LiveCompactionCheckpointStore(tmp_path)
    snapshot = [
        _turn("user", "old request"),
        _turn("assistant", "old answer"),
    ]
    prepared = [
        _turn("user", "[CONTEXT COMPACTION] old work summarized"),
        _turn("assistant", "old answer"),
    ]
    store.publish("session-a", snapshot, prepared)

    newer = [
        _turn("user", "new request while summary was running"),
        _turn("assistant", "new answer"),
    ]
    applied = store.consume_if_current("session-a", snapshot + newer)

    assert applied == prepared + newer


def test_checkpoint_rejects_changed_snapshot_without_deleting_history(tmp_path):
    store = LiveCompactionCheckpointStore(tmp_path)
    snapshot = [_turn("user", "original"), _turn("assistant", "answer")]
    store.publish("session-a", snapshot, [_turn("user", "summary")])

    changed = [_turn("user", "edited original"), _turn("assistant", "answer")]

    assert store.consume_if_current("session-a", changed) is None
    assert changed == [_turn("user", "edited original"), _turn("assistant", "answer")]


def test_stale_persisted_checkpoint_does_not_block_fresher_preparation(tmp_path):
    store = LiveCompactionCheckpointStore(tmp_path)
    old_snapshot = [_turn("user", "old"), _turn("assistant", "answer")]
    store.publish("session-a", old_snapshot, [_turn("user", "old summary")])
    changed = [_turn("user", "corrected"), _turn("assistant", "answer")]

    assert schedule_live_compaction_checkpoint(
        store=store,
        session_id="session-a",
        messages=changed,
        prepare=lambda _messages: [_turn("user", "fresh summary")],
    )

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        applied = store.consume_if_current("session-a", changed)
        if applied is not None:
            break
        time.sleep(0.01)
    assert applied == [_turn("user", "fresh summary")]


def test_checkpoint_survives_process_restart(tmp_path):
    snapshot = [
        {**_turn("user", "request"), "_db_persisted": True},
        {**_turn("assistant", "answer"), "_db_persisted": True},
    ]
    prepared = [_turn("user", "summary"), _turn("assistant", "answer")]
    LiveCompactionCheckpointStore(tmp_path).publish("session-a", snapshot, prepared)

    reloaded = LiveCompactionCheckpointStore(tmp_path)

    restored_without_runtime_markers = [
        _turn("user", "request"),
        _turn("assistant", "answer"),
    ]
    assert (
        reloaded.consume_if_current("session-a", restored_without_runtime_markers)
        == prepared
    )


def test_background_schedule_is_single_flight_and_returns_immediately(tmp_path):
    store = LiveCompactionCheckpointStore(tmp_path)
    snapshot = [_turn("user", "request"), _turn("assistant", "answer")]
    started = threading.Event()
    release = threading.Event()
    calls = []

    def prepare(messages):
        calls.append(messages)
        started.set()
        assert release.wait(2)
        return [_turn("user", "prepared summary")]

    before = time.monotonic()
    assert schedule_live_compaction_checkpoint(
        store=store,
        session_id="session-a",
        messages=snapshot,
        prepare=prepare,
    )
    assert time.monotonic() - before < 0.2
    assert started.wait(1)

    assert not schedule_live_compaction_checkpoint(
        store=store,
        session_id="session-a",
        messages=snapshot,
        prepare=prepare,
    )
    release.set()

    deadline = time.monotonic() + 2
    while store.peek("session-a") is None and time.monotonic() < deadline:
        time.sleep(0.01)

    assert len(calls) == 1
    assert json.loads(store.peek("session-a").read_text())["snapshot_length"] == 2
    assert not schedule_live_compaction_checkpoint(
        store=store,
        session_id="session-a",
        messages=snapshot,
        prepare=prepare,
    )


def test_foreground_uses_prepared_checkpoint_without_calling_summarizer(
    tmp_path, monkeypatch
):
    store = LiveCompactionCheckpointStore(tmp_path)
    snapshot = [_turn("user", "old"), _turn("assistant", "answer")]
    prepared = [_turn("user", "summary"), _turn("assistant", "answer")]
    store.publish("session-a", snapshot, prepared)
    compressor = SimpleNamespace(
        _last_compression_made_progress=False,
        _last_summary_fallback_used=False,
        _last_compress_aborted=False,
    )

    def fail_if_called(*args, **kwargs):
        pytest.fail("foreground summarizer must not run when a checkpoint is ready")

    compressor.compress = fail_if_called
    agent = SimpleNamespace(session_id="session-a", context_compressor=compressor)
    monkeypatch.setattr(
        conversation_compression, "_live_checkpoint_store", lambda _agent: store
    )

    newer = [_turn("user", "new"), _turn("assistant", "new answer")]
    result = conversation_compression._compress_messages(
        agent,
        snapshot + newer,
        approx_tokens=120,
        focus_topic=None,
        force=False,
    )

    assert result == prepared + newer
    assert compressor._last_compression_made_progress is True
    assert compressor._last_summary_fallback_used is False


def test_post_turn_scheduler_starts_only_above_soft_watermark(tmp_path, monkeypatch):
    calls = []
    store = LiveCompactionCheckpointStore(tmp_path)
    compressor = SimpleNamespace(
        threshold_tokens=100,
        last_real_prompt_tokens=69,
        last_prompt_tokens=69,
        protect_last_n=10,
    )
    agent = SimpleNamespace(
        session_id="session-a",
        platform="desktop",
        context_compressor=compressor,
        compression_enabled=True,
        compression_background_checkpoint_enabled=True,
        compression_background_checkpoint_ratio=0.70,
    )
    monkeypatch.setattr(
        conversation_compression, "_live_checkpoint_store", lambda _agent: store
    )
    monkeypatch.setattr(
        conversation_compression,
        "schedule_live_compaction_checkpoint",
        lambda **kwargs: calls.append(kwargs) or True,
    )

    assert not conversation_compression.schedule_background_compaction_checkpoint(
        agent, [_turn("user", "one"), _turn("assistant", "two")]
    )
    compressor.last_real_prompt_tokens = 70
    assert conversation_compression.schedule_background_compaction_checkpoint(
        agent, [_turn("user", "one"), _turn("assistant", "two")]
    )
    assert len(calls) == 1
