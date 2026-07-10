"""Surface the previous session's work lane to a fresh session.

``agent/lane_resolver.py`` reconstructs a lane during compression recovery.
An ordinary new chat had no equivalent: the user typed "let's continue" and the
model, holding no workspace, offered a menu of guesses.

The lane was never missing. It sits in ``session_working_state``, written at the
end of the previous session. This module reads it back and renders it as a
*hint* -- explicitly unconfirmed, because the user may well have moved on.
"""

from __future__ import annotations

from typing import Any, Optional


_FIELDS = (
    ("repo", "repo_path"),
    ("branch", "branch"),
    ("external_job", "external_job"),
    ("prompt_file", "prompt_file"),
)


def render_recent_lane_block(lane: Optional[dict[str, Any]]) -> str:
    """Render a previous session's lane as an unconfirmed hint. ``""`` when absent."""
    if not lane or not lane.get("repo_path"):
        return ""

    lines = ["<recent-lane>"]
    lines.append(
        "[System note: this is where the previous session was working. It is a "
        "hint, not the active request. If the user refers to earlier work "
        "implicitly, confirm this is the lane they mean before reading or "
        "changing any file — and never infer a workspace from a directory "
        "listing instead.]"
    )
    for label, key in _FIELDS:
        value = lane.get(key)
        if value:
            lines.append(f"- {label}: {value}")
    for note in lane.get("source_of_truth") or []:
        lines.append(f"- source_of_truth: {note}")
    lines.append("</recent-lane>")
    return "\n".join(lines)
