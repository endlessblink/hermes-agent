"""Deterministic ('mechanical') fact capture from a conversation.

These facts are ground truth taken from what actually happened -- the repo an
agent job ran against, the command it ran, and explicit statements the user
made in their own words. No model judgment is involved, so they cannot be
hallucinated, and they are stored at the ``mechanical`` (highest) provenance
tier.

Model-*inferred* facts (decisions, rationale, rejected approaches read between
the lines) are a separate, lower-trust path -- not this module.

Pure and I/O-free: callers pass the message list and store the results.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# Explicit, first-person user statements. These are the user's literal words,
# so matching them is deterministic capture, not inference.
_PREFERENCE_RE = [
    re.compile(r"\bI\s+(?:prefer|like|love|use|want|need|hate|dislike|avoid)\s+(.+)", re.I),
    re.compile(r"\bmy\s+(?:favorite|preferred|default)\s+\w+\s+is\s+(.+)", re.I),
    re.compile(r"\bI\s+(?:always|never|usually)\s+(.+)", re.I),
]
_DECISION_RE = [
    re.compile(r"\bwe\s+(?:decided|agreed|chose)\s+(?:to\s+)?(.+)", re.I),
    re.compile(r"\bthe\s+project\s+(?:uses|needs|requires)\s+(.+)", re.I),
]

# Corrections the user gives mid-conversation ("don't do X", "this is wrong",
# "these errors should not repeat"). High-signal: they must be remembered so the
# mistake isn't repeated. Bilingual (English + Hebrew). Narrow on purpose.
_CORRECTION_RE = [
    re.compile(r"\b(?:don'?t|do\s+not|please\s+don'?t)\s+(.+)", re.I),
    re.compile(r"\b(?:should\s*n'?t|shouldn'?t|must\s*n'?t|mustn'?t|should\s+not|must\s+not)\s+(.+)", re.I),
    re.compile(r"\b(?:stop|avoid|no\s+more|no\s+longer)\s+(.+)", re.I),
    re.compile(r"\b(?:instead\s+of|rather\s+than)\s+(.+)", re.I),
    re.compile(r"\b(?:should\s+not\s+repeat|do\s*n'?t\s+repeat|never\s+again|not\s+again)\b", re.I),
    re.compile(r"\b(?:this|that|it)\s+(?:is|'?s|was)\s+wrong\b", re.I),
    re.compile(
        r"(?:אל\s+ת\w*|לא\s+ל\w+|אף\s+פעם|במקום|תפסיק|תפסיקי|לא\s+לחזור|"
        r"שוב\s+נתקעת|זו?\s+טעות|לא\s+רוצה|לא\s+נכון|תפסיק\s+ל)",
        re.UNICODE,
    ),
]

_MIN_STATEMENT_LEN = 10
_MAX_CONTENT = 400


@dataclass
class MechanicalFact:
    content: str
    category: str  # 'workspace' | 'command' | 'user_pref' | 'project'
    source_message_id: int | None = None


def _msg_id(msg: dict) -> int | None:
    for key in ("id", "message_id", "db_id"):
        value = msg.get(key)
        if isinstance(value, int):
            return value
    return None


def _workspace_facts(messages) -> list[MechanicalFact]:
    """Repo, branch, and agent-launch commands, from the transcript alone.

    Reuses the lane resolver's transcript scan -- the same ground truth the
    recovery/recall path uses -- so 'what repo were we in' is captured as a
    durable fact instead of re-derived by guessing each session.
    """
    try:
        from agent.lane_resolver import resolve_lane
    except Exception:
        return []
    # No cwd / process list: this scans agent-launch commands in the transcript,
    # which is the deterministic part.
    lane = resolve_lane(messages or [], session_cwd="")
    if lane is None or not lane.repo_path:
        return []
    facts: list[MechanicalFact] = []
    branch = f" (branch {lane.branch})" if lane.branch else ""
    facts.append(MechanicalFact(f"Work is happening in the repo {lane.repo_path}{branch}.", "workspace"))
    if lane.external_job:
        facts.append(MechanicalFact(f"Ran: {lane.external_job}", "command"))
    if lane.prompt_file:
        facts.append(MechanicalFact(f"Used prompt file {lane.prompt_file}.", "command"))
    return facts


def _statement_facts(messages) -> list[MechanicalFact]:
    facts: list[MechanicalFact] = []
    for msg in messages or []:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, str) or len(content) < _MIN_STATEMENT_LEN:
            continue
        mid = _msg_id(msg)
        # Corrections first — highest signal, must not be missed even if the
        # sentence also happens to match a preference pattern.
        if any(p.search(content) for p in _CORRECTION_RE):
            facts.append(MechanicalFact(content[:_MAX_CONTENT], "correction", mid))
        elif any(p.search(content) for p in _PREFERENCE_RE):
            facts.append(MechanicalFact(content[:_MAX_CONTENT], "user_pref", mid))
        elif any(p.search(content) for p in _DECISION_RE):
            facts.append(MechanicalFact(content[:_MAX_CONTENT], "project", mid))
    return facts


def mechanical_facts(messages) -> list[MechanicalFact]:
    """All deterministic facts from a conversation. Deduped by content."""
    seen: set[str] = set()
    out: list[MechanicalFact] = []
    for fact in (*_workspace_facts(messages), *_statement_facts(messages)):
        if fact.content and fact.content not in seen:
            seen.add(fact.content)
            out.append(fact)
    return out
