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

# Spans where a question mark is punctuation or data, never a question to the
# user: fenced code, inline code, blockquotes, URLs, and Markdown links.
_FENCED_CODE_RE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_INDENTED_CODE_RE = re.compile(r"^(?: {4}|\t).*$", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_BLOCKQUOTE_RE = re.compile(r"^[ \t]*>.*$", re.MULTILINE)
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)

# Structured lines carry checklists, criteria, and tables. A question mark there
# is an item being listed, not the turn stopping to ask the user something.
_STRUCTURED_LINE_RE = re.compile(
    r"^[ \t]*(?:[-*+•]|\d+[.)]|#{1,6}\s|\|)",
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?؟。])\s+")


def _strip_non_prose(text: str) -> str:
    """Blank out spans where a `?` cannot be a question aimed at the user."""

    for pattern in (
        _FENCED_CODE_RE,
        _INDENTED_CODE_RE,
        _INLINE_CODE_RE,
        _BLOCKQUOTE_RE,
        _URL_RE,
    ):
        text = pattern.sub(lambda match: " " * len(match.group(0)), text)
    return text


def _closing_prose(text: str) -> str:
    """Return the trailing prose the turn actually ends on.

    Trailing structured lines (checklist items, table rows, headings) are
    skipped so a closing question is still detected when it sits just above a
    list, but the list items themselves never trigger the gate.
    """

    lines = _strip_non_prose(text).splitlines()
    prose_lines: list[str] = []
    for line in reversed(lines):
        if not line.strip():
            if prose_lines:
                break
            continue
        if _STRUCTURED_LINE_RE.match(line):
            if prose_lines:
                break
            continue
        prose_lines.append(line)
    if not prose_lines:
        return ""

    paragraph = " ".join(reversed(prose_lines)).strip()
    sentences = [part for part in _SENTENCE_SPLIT_RE.split(paragraph) if part.strip()]
    return sentences[-1] if sentences else paragraph


def _asks_the_user_a_question(response: object) -> bool:
    """True only when the response closes by directing a question at the user."""

    text = str(response or "")
    if not _QUESTION_MARK_RE.search(text):
        return False
    if _HERMES_UI_FENCE_RE.fullmatch(text):
        # A hermes-ui artifact is a rendered question control, not prose. Leave
        # it to the artifact branch below to accept or reject.
        return True
    return bool(_QUESTION_MARK_RE.search(_closing_prose(text)))


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

    if not _asks_the_user_a_question(response):
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
        "The response ends by asking the user a question. The prose you already "
        "wrote has been delivered to the user and is visible in the chat — do not "
        "repeat it. Do not ask in assistant prose, Markdown, or a hermes-ui "
        "artifact — call the `clarify` tool with one focused question, concise "
        "choices when useful, and a custom answer path.",
    )


__all__ = [
    "DesktopClarifyGateDecision",
    "evaluate_desktop_clarify_output",
]
