"""Prevent Desktop turns from stranding user-directed questions in prose."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Collection


_QUESTION_MARK_RE = re.compile(r"[?؟]")
_HERMES_UI_FENCE_RE = re.compile(
    r"^\s*```hermes-ui\s*\n(?P<body>.*?)\n```\s*$", re.DOTALL
)


@dataclass(frozen=True)
class DesktopClarifyGateDecision:
    accepted: bool
    reason: str = ""
    retry_instruction: str = ""


def evaluate_desktop_clarify_output(
    response: object,
    *,
    platform: str,
    valid_tool_names: Collection[str],
    allow_personal_assistant_interview_artifact: bool = False,
) -> DesktopClarifyGateDecision:
    """Require Desktop questions to travel through the blocking clarify tool."""

    if platform.strip().lower() != "desktop" or "clarify" not in valid_tool_names:
        return DesktopClarifyGateDecision(True)

    if not _QUESTION_MARK_RE.search(str(response or "")):
        return DesktopClarifyGateDecision(True)

    if allow_personal_assistant_interview_artifact:
        match = _HERMES_UI_FENCE_RE.fullmatch(str(response or ""))
        if match:
            try:
                artifact = json.loads(match.group("body"))
            except (TypeError, ValueError, json.JSONDecodeError):
                artifact = None
            if isinstance(artifact, dict) and artifact.get("type") == "task-profile-review":
                # The Personal Assistant output gate validates this durable card
                # against the authoritative interview immediately afterwards.
                return DesktopClarifyGateDecision(True)

    return DesktopClarifyGateDecision(
        False,
        "prose_question_requires_clarify",
        "The response contains a question directed at the user. Do not ask it in "
        "assistant prose, Markdown, or a hermes-ui artifact. Call the `clarify` tool "
        "with one focused question, concise choices when useful, and a custom answer path.",
    )


__all__ = ["DesktopClarifyGateDecision", "evaluate_desktop_clarify_output"]
