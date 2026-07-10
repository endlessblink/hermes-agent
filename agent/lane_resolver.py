"""Resolve the active work lane for a continuation, from structured evidence.

Compression recovery (``tui_gateway/server.py::_build_dropoff_seed``) starts a
fresh session whose ``cwd`` falls back to the user's home directory, then
replays the user's pending prompt into it. When that prompt is a vague
continuation ("Codex finished, can you check?"), the model has to reconstruct
the workspace on its own -- and the cheapest thing it reaches for is a
filesystem probe, which is exactly the least reliable evidence available.

This module ranks the evidence instead. Provenance decides, not lexical
relevance:

    process-registry  a live external-agent job          high
    transcript        an agent launch command in history high
    session-cwd       a cwd that is actually a repo      low

A filesystem probe is not a tier. It never enters.

Resolution is pure and does no I/O of its own -- the recovery path runs under a
12s budget that has already been exhausted in the wild. ``is_repo`` and the
process list are injected by the caller.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional


# Binaries whose invocations name a workspace we should trust. A bare ``--cd``
# is not evidence -- other tools use the flag for unrelated purposes.
_AGENT_BINARIES = ("codex", "claude", "opencode", "aider", "cursor-agent", "gemini")

_AGENT_LINE_RE = re.compile(
    r"\b(?:" + "|".join(_AGENT_BINARIES) + r")\b[^\n]*", re.IGNORECASE
)

# ``--cd <path>`` / ``--cwd <path>`` / ``-C <path>`` / ``--add-dir <path>``
_WORKDIR_FLAG_RE = re.compile(
    r"(?:--cd|--cwd|--add-dir|--project-dir|-C)[=\s]+((?:'[^']+')|(?:\"[^\"]+\")|(?:\S+))"
)

# ``- < /tmp/foo-prompt.md`` or ``--prompt-file /tmp/foo.md``
_PROMPT_FILE_RE = re.compile(
    r"(?:<\s*|--prompt-file[=\s]+)((?:'[^']+')|(?:\"[^\"]+\")|(?:\S+\.(?:md|txt|prompt)))"
)

_SCORE_PROCESS_REGISTRY = 80
_SCORE_TRANSCRIPT = 70
_SCORE_SESSION_CWD = 40

# Two candidates within this margin are not distinguishable by provenance.
_AMBIGUITY_MARGIN = 20


@dataclass
class Lane:
    repo_path: str
    source: str
    score: int = 0
    branch: Optional[str] = None
    prompt_file: Optional[str] = None
    external_job: Optional[str] = None
    confidence: str = "high"
    alternatives: list["Lane"] = field(default_factory=list)


def _unquote(value: str) -> str:
    value = value.strip().rstrip(",;)")
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _is_home(path: str) -> bool:
    """The recovery fallback cwd. Never a workspace."""
    try:
        return os.path.realpath(path) == os.path.realpath(os.path.expanduser("~"))
    except OSError:
        return False


def _mentions_agent(text: str) -> bool:
    return bool(_AGENT_LINE_RE.search(text or ""))


def _scan_agent_launches(history: Iterable[dict]) -> list[Lane]:
    """Pull workspace paths out of agent launch commands in the transcript.

    This is the durable record. A job that has already exited -- as the Codex
    job had, when the failing turn ran -- is gone from the process registry but
    still present here.
    """
    found: list[Lane] = []
    for msg in history or []:
        content = msg.get("content")
        if not isinstance(content, str):
            continue
        for line in _AGENT_LINE_RE.findall(content):
            flag = _WORKDIR_FLAG_RE.search(line)
            if not flag:
                continue
            repo = _unquote(flag.group(1))
            if not repo.startswith("/") or _is_home(repo):
                continue
            prompt = _PROMPT_FILE_RE.search(line)
            found.append(
                Lane(
                    repo_path=repo,
                    source="transcript",
                    score=_SCORE_TRANSCRIPT,
                    prompt_file=_unquote(prompt.group(1)) if prompt else None,
                    external_job=_redact_command(line),
                )
            )
    return found


# Flags whose values describe the lane and are safe to echo back. Every other
# flag keeps its name and loses its value -- a lane record is not worth leaking
# `--token sk-...` into a system message.
_VALUE_SAFE_FLAGS = frozenset(
    {"--cd", "--cwd", "-C", "--add-dir", "--project-dir", "--sandbox", "--model", "--profile"}
)


def _redact_command(line: str) -> str:
    """Keep the binary, its subcommand, and its flags. Drop operands and secret values."""
    try:
        parts = shlex.split(line)
    except ValueError:
        parts = line.split()
    if not parts:
        return ""

    keep = [parts[0]]
    # A leading subcommand (`codex exec`) is part of the command's identity.
    index = 1
    if len(parts) > 1 and not parts[1].startswith("-"):
        keep.append(parts[1])
        index = 2

    while index < len(parts) and len(keep) < 8:
        part = parts[index]
        if part.startswith("-"):
            flag, _, inline = part.partition("=")
            if inline and flag not in _VALUE_SAFE_FLAGS:
                keep.append(flag)
            else:
                keep.append(part)
            takes_value = index + 1 < len(parts) and not parts[index + 1].startswith("-")
            if takes_value and not inline:
                if flag in _VALUE_SAFE_FLAGS:
                    keep.append(parts[index + 1])
                index += 1
        index += 1
    return " ".join(keep)


def _scan_processes(process_sessions: Iterable[dict]) -> list[Lane]:
    found: list[Lane] = []
    for proc in process_sessions or []:
        command = str(proc.get("command") or "")
        cwd = str(proc.get("cwd") or "")
        if not command or not cwd or not _mentions_agent(command) or _is_home(cwd):
            continue
        found.append(
            Lane(
                repo_path=cwd,
                source="process-registry",
                score=_SCORE_PROCESS_REGISTRY,
                external_job=_redact_command(command),
            )
        )
    return found


def resolve_lane(
    history: Iterable[dict],
    *,
    session_cwd: str = "",
    process_sessions: Optional[Iterable[dict]] = None,
    is_repo: Optional[Callable[[str], bool]] = None,
    branch_probe: Optional[Callable[[str], Optional[str]]] = None,
) -> Optional[Lane]:
    """Rank lane candidates by provenance. Returns ``None`` when nothing qualifies.

    ``None`` is a real answer, and the caller must surface it -- an unresolved
    lane is what the model needs to know before it starts probing the disk.
    """
    candidates: list[Lane] = []
    candidates.extend(_scan_processes(process_sessions or []))
    # Later launches supersede earlier ones.
    candidates.extend(reversed(_scan_agent_launches(history)))

    if session_cwd and not _is_home(session_cwd):
        if is_repo is None or is_repo(session_cwd):
            candidates.append(
                Lane(repo_path=session_cwd, source="session-cwd", score=_SCORE_SESSION_CWD)
            )

    if not candidates:
        return None

    # Stable sort: score desc, original order preserved within a tier.
    ranked = sorted(candidates, key=lambda c: -c.score)
    best = ranked[0]

    rivals = [c for c in ranked[1:] if c.repo_path != best.repo_path]
    if rivals and best.score - rivals[0].score < _AMBIGUITY_MARGIN:
        best.confidence = "ambiguous"
        seen = {best.repo_path}
        for rival in rivals:
            if rival.repo_path not in seen:
                seen.add(rival.repo_path)
                best.alternatives.append(rival)
    else:
        best.confidence = "high" if best.score >= _SCORE_TRANSCRIPT else "low"

    if branch_probe is not None:
        try:
            best.branch = branch_probe(best.repo_path)
        except Exception:
            best.branch = None

    return best


def echoes_recovery_reason(snippet: str, error_message: str, threshold: float = 0.8) -> bool:
    """True when a continuity hit is just the recovery notice repeated back.

    Continuity recall is queried with the recovery reason as part of its search
    string, so it reliably retrieves earlier dropoff records -- whose content is
    that same reason. The model receives its own error message where the work
    should be. Worse than empty: it consumes attention.
    """
    if not error_message:
        return False
    words = re.findall(r"\w+", snippet.lower())
    if not words:
        return True
    reason = set(re.findall(r"\w+", error_message.lower()))
    if not reason:
        return False
    overlap = sum(1 for word in words if word in reason)
    return overlap / len(words) >= threshold


_NO_LANE_BLOCK = (
    '<active-lane confidence="none">\n'
    "No active work lane could be resolved for this session.\n"
    "If the user's message refers to earlier work implicitly ('continue', "
    "'Codex finished', 'check it'), do not infer the workspace from a "
    "filesystem probe, a directory listing, or the first repository you find — "
    "those are not evidence. Ask the user which project they mean, offering the "
    "full path of each candidate you can justify.\n"
    "</active-lane>"
)


def render_lane_block(lane: Optional[Lane]) -> str:
    """Render the lane as a fenced, machine-readable block for the recovery preamble."""
    if lane is None:
        return _NO_LANE_BLOCK

    lines = [f'<active-lane confidence="{lane.confidence}" source="{lane.source}">']
    lines.append(f"repo: {lane.repo_path}")
    if lane.branch:
        lines.append(f"branch: {lane.branch}")
    if lane.external_job:
        lines.append(f"external_job: {lane.external_job}")
    if lane.prompt_file:
        lines.append(f"prompt_file: {lane.prompt_file}")
    for alt in lane.alternatives:
        lines.append(f"alternative: {alt.repo_path} (via {alt.source})")
    if lane.confidence == "ambiguous":
        lines.append(
            "More than one lane is equally supported. Ask the user which one "
            "they mean before reading or modifying any file."
        )
    elif lane.confidence == "low":
        lines.append("Weak evidence. Verify with git before acting on it.")
    else:
        lines.append("Verify with git before acting on it.")
    lines.append("</active-lane>")
    return "\n".join(lines)
