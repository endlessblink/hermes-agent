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


def test_proactive_context_falls_back_to_legacy_gateway_namespace(monkeypatch):
    _patch_target(monkeypatch)
    now = 1_000_000.0

    class LegacyOnlyDB(_SessionDB):
        def find_latest_gateway_session_for_peer(self, **kwargs):
            if kwargs["session_key"].startswith("agent:main:"):
                return {"id": "session-legacy"}
            return None

    db = LegacyOnlyDB([
        {
            "role": "user",
            "platform_message_id": "fresh-1",
            "timestamp": now - 60,
            "content": "היום הצלחתי לעצור לרגע ולהרגיש קצת יותר קל",
        }
    ])
    monkeypatch.setattr(scheduler.time, "time", lambda: now)

    context = scheduler._lifeboat_recent_context(_job(), db)

    assert "היום הצלחתי לעצור לרגע" in context


def test_proactive_context_reads_shared_root_store_when_profile_store_is_empty(monkeypatch):
    _patch_target(monkeypatch)
    now = 1_000_000.0

    class EmptyProfileDB(_SessionDB):
        def find_latest_gateway_session_for_peer(self, **_kwargs):
            return None

    class RootDB(_SessionDB):
        def find_latest_gateway_session_for_peer(self, **kwargs):
            if kwargs["session_key"].startswith("agent:life-advisor:"):
                return {"id": "session-root"}
            return None

    import hermes_state

    monkeypatch.setattr(hermes_state, "SessionDB", lambda *_args, **_kwargs: RootDB([
        {
            "role": "user",
            "platform_message_id": "fresh-root-1",
            "timestamp": now - 60,
            "content": "הצלחתי לנוח קצת אחרי יום עמוס",
        }
    ]))
    monkeypatch.setattr(scheduler, "get_default_hermes_root", lambda: type("Root", (), {
        "__truediv__": lambda self, _name: "state.db",
    })())
    monkeypatch.setattr(scheduler.time, "time", lambda: now)

    context = scheduler._lifeboat_recent_context(_job(), EmptyProfileDB([]))

    assert "הצלחתי לנוח קצת" in context


def test_proactive_context_fails_closed_without_recent_provenance(monkeypatch):
    _patch_target(monkeypatch)
    now = 1_000_000.0
    db = _SessionDB([
        {"role": "user", "platform_message_id": "missing-time", "content": "אין חותמת זמן"},
        {"role": "user", "platform_message_id": "old-1", "timestamp": now - 90_000, "content": "אירוע ישן"},
    ])
    monkeypatch.setattr(scheduler.time, "time", lambda: now)

    assert scheduler._lifeboat_recent_context(_job(), db) == ""


def test_generic_period_request_does_not_pull_an_unrelated_recent_project(monkeypatch):
    _patch_target(monkeypatch)
    now = 1_000_000.0
    db = _SessionDB([
        {"role": "user", "platform_message_id": "old-1", "timestamp": now - 60, "content": "הווידאו של Too Much"},
        {"role": "user", "platform_message_id": "new-1", "timestamp": now - 30, "content": "אני רוצה לעשות דיבריף על השבוע האחרון"},
    ])
    monkeypatch.setattr(scheduler.time, "time", lambda: now)

    context = scheduler._lifeboat_recent_context(_job(), db)

    assert "השבוע האחרון" in context
    assert "Too Much" not in context


def test_explicit_past_reference_can_bridge_to_a_shared_subject(monkeypatch):
    _patch_target(monkeypatch)
    now = 1_000_000.0
    db = _SessionDB([
        {"role": "user", "platform_message_id": "old-1", "timestamp": now - 60, "content": "הווידאו של Too Much נשאר לי בראש"},
        {"role": "user", "platform_message_id": "new-1", "timestamp": now - 30, "content": "בוא נדבר שוב על הווידאו של Too Much"},
    ])
    monkeypatch.setattr(scheduler.time, "time", lambda: now)

    context = scheduler._lifeboat_recent_context(_job(), db)

    assert context.count("Too Much") >= 2


def test_proactive_guard_requires_relation_for_old_events(monkeypatch):
    _patch_target(monkeypatch)
    guard = scheduler._lifeboat_proactive_guard(_job())

    assert "older event may be raised only when the current user words explicitly connect" in guard
    assert "do not return [SILENT]" in guard


def test_proactive_job_skips_without_trusted_recent_context(monkeypatch, caplog):
    caplog.set_level("INFO", logger="cron.scheduler")
    _patch_target(monkeypatch)
    db = _SessionDB([
        {"role": "assistant", "timestamp": 1_000_000.0, "content": "old bot question"},
        {"role": "user", "timestamp": 1_000_000.0 - 90_000, "content": "old event"},
    ])

    assert scheduler._build_job_prompt(_job(), session_db=db) is None
    assert "phase=prompt_suppressed" in caplog.text
    assert "reason=no_trusted_recent_context" in caplog.text
