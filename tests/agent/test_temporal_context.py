from datetime import datetime
from zoneinfo import ZoneInfo

from agent.temporal_context import build_live_temporal_context


def test_live_temporal_context_uses_the_current_local_moment():
    context = build_live_temporal_context(
        datetime(2026, 7, 19, 22, 35, 12, tzinfo=ZoneInfo("Asia/Jerusalem"))
    )

    assert "2026-07-19T22:35:12+03:00" in context
    assert "Sunday" in context
    assert "Asia/Jerusalem" in context
    assert '"This week"' in context
    assert "must contain 2026-07-19" in context
    assert '"next week" is the immediately following calendar week' in context


def test_live_temporal_context_rolls_forward_instead_of_reusing_a_session_date():
    before_midnight = build_live_temporal_context(
        datetime(2026, 7, 19, 23, 59, 59, tzinfo=ZoneInfo("Asia/Jerusalem"))
    )
    after_midnight = build_live_temporal_context(
        datetime(2026, 7, 20, 0, 0, 1, tzinfo=ZoneInfo("Asia/Jerusalem"))
    )

    assert "must contain 2026-07-19" in before_midnight
    assert "must contain 2026-07-20" in after_midnight
    assert before_midnight != after_midnight
