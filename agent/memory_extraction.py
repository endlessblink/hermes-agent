"""Model-inferred fact extraction from a conversation.

Mechanical capture (``agent/memory_capture.py``) only gets what is literally in
the transcript -- the repo, the command. The *substance* of a working session
lives between the lines: what we're actually working on, lessons the user asked
to be remembered, changes they asked for, decisions and why, approaches tried
and rejected, and what's still open. Pulling those out is a reading task, so it
needs the model.

These facts are stored at the ``inferred`` (lower) provenance tier and linked to
their source, so a human/Obsidian edit always overrides them and any claim is
checkable. Runs at session end, off the turn's critical path.

Split so the model-free parts (prompt, transcript rendering, parsing) are unit
tested without a live model; only ``extract_inferred_facts`` makes the call.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


# The categories the user actually asked to be remembered, in the model's words.
CATEGORIES = {
    "subject": "What we are working on right now — the active task, goal, or topic.",
    "lesson": "A lesson or instruction the user asked to be remembered or learned "
              "('remember to…', 'from now on…', 'always/never…', a correction).",
    "change": "A concrete change the user asked to be implemented (a feature, fix, edit).",
    "decision": "A decision that was made, and the reason for it.",
    "rejected": "An approach that was tried or considered and rejected, and why.",
    "preference": "A durable preference or working-style the user revealed.",
    "open_thread": "Something left unfinished or blocked when the session ended.",
}

_EXTRACTION_INSTRUCTIONS = (
    "You are extracting durable memory from a work conversation so a future "
    "session can continue without re-asking. Read the transcript and record only "
    "facts that will still matter next time — skip pleasantries, one-off "
    "questions, and anything already obvious.\n\n"
    "For each fact output an object with:\n"
    '  "category": one of ' + ", ".join(CATEGORIES) + "\n"
    '  "content": one self-contained sentence, understandable with no other '
    "context (name the project/thing explicitly; do not write 'it' or 'this').\n\n"
    "Categories:\n"
    + "\n".join(f"  {k}: {v}" for k, v in CATEGORIES.items())
    + "\n\nReturn ONLY a JSON array of such objects. If nothing is worth "
    "remembering, return []. Do not invent facts that were not stated or clearly "
    "implied. Write each fact in the language the user was using."
)

_MAX_TRANSCRIPT_CHARS = 24000
_MAX_FACTS = 40


@dataclass
class InferredFact:
    content: str
    category: str


def render_transcript(messages, limit: int = _MAX_TRANSCRIPT_CHARS) -> str:
    """Flatten a message list to a plain user/assistant transcript for the model."""
    lines: list[str] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content")
        if isinstance(content, list):
            content = " ".join(
                str(p.get("text") or p.get("content") or "")
                for p in content
                if isinstance(p, dict)
            )
        if not isinstance(content, str) or not content.strip():
            continue
        lines.append(f"{role}: {content.strip()}")
    text = "\n\n".join(lines)
    if len(text) > limit:
        # Keep the tail — the end of a session holds the freshest decisions and
        # open threads.
        text = "…[earlier omitted]…\n\n" + text[-limit:]
    return text


def build_extraction_messages(messages) -> list[dict]:
    """The chat messages sent to the extraction model. Model-free; unit-testable."""
    transcript = render_transcript(messages)
    return [
        {"role": "system", "content": _EXTRACTION_INSTRUCTIONS},
        {"role": "user", "content": f"Transcript:\n\n{transcript}"},
    ]


def parse_facts(raw: str) -> list[InferredFact]:
    """Parse the model's JSON array into facts. Tolerant of code fences / stray prose."""
    if not raw or not raw.strip():
        return []
    text = raw.strip()
    # Strip ```json … ``` fences if present.
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    # Grab the first JSON array in the string.
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[InferredFact] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        category = str(item.get("category") or "").strip().lower()
        if not content or category not in CATEGORIES:
            continue
        if content in seen:
            continue
        seen.add(content)
        out.append(InferredFact(content=content[:400], category=category))
        if len(out) >= _MAX_FACTS:
            break
    return out


def extract_inferred_facts(messages) -> list[InferredFact]:
    """Call the model to extract inferred facts. Returns [] on any failure.

    Uses the same synchronous ``call_llm`` path compression already invokes from
    a background thread, under the ``memory_extraction`` auxiliary task.
    """
    if not messages:
        return []
    try:
        from agent.auxiliary_client import call_llm
    except Exception:
        return []
    try:
        response = call_llm(
            task="memory_extraction",
            messages=build_extraction_messages(messages),
            temperature=0.0,
        )
        message = response.choices[0].message
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
        return parse_facts(content if isinstance(content, str) else str(content or ""))
    except Exception:
        return []
