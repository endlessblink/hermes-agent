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

# A `?` inside a link or a code sample is punctuation, not a question aimed at
# the user. Scanning the raw text made every answer that merely quoted a URL
# query string or a regex look like an unasked question, and the gate then
# discarded the whole answer. Strip those spans before scanning.
# `hermes-ui` fences are deliberately kept: the gate must still catch questions
# smuggled into an artifact.
_CODE_FENCE_RE = re.compile(
    r"```(?P<info>[^\n`]*)\n(?P<body>.*?)(?:```|\Z)", re.DOTALL
)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_BLOCKQUOTE_RE = re.compile(r"^[ \t]*>.*$", re.MULTILINE)

# Structured lines carry checklists, criteria, and tables. A question mark there
# is an item being listed, not the turn stopping to ask the user something.
_STRUCTURED_LINE_RE = re.compile(r"^[ \t]*(?:[-*+•]|\d+[.)]|#{1,6}\s|\|)")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?؟。])\s+")


def _strip_non_prose_spans(text: str) -> str:
    """Blank out code, links, and quotes so only user-facing prose is scanned."""

    def _replace_fence(match: re.Match[str]) -> str:
        if match.group("info").strip().lower() == "hermes-ui":
            return match.group(0)
        return "\n"

    without_code = _CODE_FENCE_RE.sub(_replace_fence, text)
    without_code = _INLINE_CODE_RE.sub(" ", without_code)
    without_code = _BLOCKQUOTE_RE.sub(" ", without_code)
    return _URL_RE.sub(" ", without_code)


def _closing_prose(text: str) -> str:
    """Return the trailing prose the turn actually ends on.

    Trailing structured lines (checklist items, table rows, headings) are
    skipped so a closing question is still caught when it sits just above a
    list, while the list items themselves never trigger the gate.
    """

    prose_lines: list[str] = []
    for line in reversed(text.splitlines()):
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
    """True only when the response closes by directing a question at the user.

    A question mark alone is not a question: checklists phrased as questions,
    rhetorical asides, and quoted lines all carry one, and treating those as
    unasked questions made the gate discard complete answers the user had
    already read.
    """

    text = str(response or "")
    scanned = _strip_non_prose_spans(text)
    if not _QUESTION_MARK_RE.search(scanned):
        return False

    # An artifact is a rendered question control rather than prose — leave it to
    # the artifact branch to accept or reject.
    if _HERMES_UI_FENCE_RE.fullmatch(text):
        return True

    # Questions smuggled into a hermes-ui fence mid-response still count.
    for fence in _CODE_FENCE_RE.finditer(scanned):
        if fence.group("info").strip().lower() == "hermes-ui" and _QUESTION_MARK_RE.search(
            fence.group(0)
        ):
            return True

    return bool(_QUESTION_MARK_RE.search(_closing_prose(scanned)))


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


__all__ = ["DesktopClarifyGateDecision", "evaluate_desktop_clarify_output"]
