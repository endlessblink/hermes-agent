"""Tests for the bounded context bridge used by Life-Boat proactive jobs."""

from cron import scheduler


class _SessionDB:
    def __init__(self, messages):
        self.messages = messages

    def find_latest_gateway_session_for_peer(self, **_kwargs):
        return {"id": "session-1"}

    def get_messages(self, _session_id, limit=None):
        return self.messages[:limit] if limit else list(self.messages)


def _job():
    return {"deliver": "telegram:-1004230590253:2"}


def _patch_target(monkeypatch):
    monkeypatch.setattr(scheduler, "_get_hermes_home", lambda: type("H", (), {"name": "life-advisor"})())
    monkeypatch.setattr(
        scheduler,
        "_resolve_delivery_targets",
        lambda _job: [{"platform": "telegram", "chat_id": "-1004230590253", "thread_id": "2"}],
    )


def test_proactive_context_uses_recent_user_words_only(monkeypatch):
    _patch_target(monkeypatch)
    now = 1_000_000.0
    db = _SessionDB([
        {"role": "user", "platform_message_id": "old-1", "timestamp": now - 90_000, "content": "הווידאו ל Too Much"},
        {"role": "assistant", "timestamp": now - 60, "content": "בוא נחשוב על הדימוי"},
        {"role": "user", "platform_message_id": "new-1", "timestamp": now - 60, "content": "היום הרגשתי יותר קל"},
    ])
    monkeypatch.setattr(scheduler.time, "time", lambda: now)

    context = scheduler._lifeboat_recent_context(_job(), db)

    assert "היום הרגשתי יותר קל" in context
    assert "Too Much" not in context
    assert "בוא נחשוב" not in context
    assert "assistant:" not in context


def test_proactive_context_fails_closed_without_recent_provenance(monkeypatch):
    _patch_target(monkeypatch)
    now = 1_000_000.0
    db = _SessionDB([
        {"role": "user", "platform_message_id": "missing-time", "content": "אין חותמת זמן"},
        {"role": "user", "platform_message_id": "old-1", "timestamp": now - 90_000, "content": "אירוע ישן"},
    ])
    monkeypatch.setattr(scheduler.time, "time", lambda: now)

    assert scheduler._lifeboat_recent_context(_job(), db) == ""
