"""Fresh per-turn temporal grounding for relative date language."""

from __future__ import annotations

from datetime import datetime

from hermes_time import now as hermes_now


def build_live_temporal_context(at: datetime | None = None) -> str:
    """Describe the user's current local moment without persisting it.

    System prompts are intentionally reused across a long-lived session. This
    block belongs on the current API-only user turn so relative phrases keep
    advancing even when the conversation crosses an hour, day, or week.
    """
    current = at or hermes_now()
    local_date = current.date().isoformat()
    timezone_name = getattr(current.tzinfo, "key", None) or current.tzname() or "local"

    return "\n".join(
        (
            "<live-temporal-context>",
            f"Local timestamp: {current.isoformat(timespec='seconds')}",
            f"Local weekday: {current.strftime('%A')}",
            f"Timezone: {timezone_name}",
            (
                "Resolve today, tomorrow, this week, next week, and other relative periods from this local moment. "
                f'"This week" is the user\'s current calendar week and must contain {local_date}; '
                '"next week" is the immediately following calendar week. Use the user\'s locale and calendar '
                "convention for week boundaries. Never silently relabel the next full week as this week."
            ),
            "</live-temporal-context>",
        )
    )
