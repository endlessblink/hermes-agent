"""Pre-action gate: don't act on an unconfirmed project.

Hermes keeps presuming which project a vague message refers to and then reading
old notes or editing files for the wrong one. This gate makes it confirm first.

It arms only when BOTH are true:
  * the user's message is a vague continuation ("let's continue", "check it",
    "קודקס סיים", "תמשיך") — not a specific instruction, and
  * the session has no confirmed work-lane yet (we don't already know the repo).

When armed, tools that read or change a repo are refused with a message telling
the model to confirm the project via `clarify` first. The escape hatches stay
open — `clarify` itself, session/vault search, read-only git — so the model can
gather candidates and ask. Calling `clarify` satisfies the gate for the rest of
the turn, so it can never loop.

Reliability contract: this module is pure and every entry point fails OPEN. A
bug here must never block a tool — worse than the problem it solves. Call sites
wrap it in try/except returning "don't block".
"""

from __future__ import annotations

import re


# A vague continuation refers to prior work without naming it. Bilingual
# (English + Hebrew), deliberately narrow: a specific instruction like "add a
# dark-mode toggle to the header" must NOT match, or the gate over-fires.
_VAGUE_CONTINUATION_RE = re.compile(
    r"""
    \b(
        continue | carry\s+on | keep\s+going | go\s+on | pick\s+up\s+where |
        what\s+we\s+started | where\s+we\s+left | finish(?:\s+it)? | finished |
        (?:can\s+you\s+)?check(?:\s+it)? | the\s+repo | that\s+project | resume
    )\b
    |
    (?:תמשיך | תמשיכי | סיים | סיימת | סיימנו | תבדוק | תבדקי |
       מה\s*שהתחלנו | איפה\s*שעצרנו | הפרויקט | הריפו | תמשיך\s*מ)
    """,
    re.IGNORECASE | re.VERBOSE | re.UNICODE,
)

# Names a concrete target the user pointed at — an absolute path or an explicit
# "in the <name> project/repo". If present, it isn't vague; don't arm.
_EXPLICIT_TARGET_RE = re.compile(
    r"(?:/[\w.\-]+/[\w./\-]+)|(?:\b(?:in|on|for)\s+the\s+[\w\-]+\s+(?:repo|project|folder|directory)\b)",
    re.IGNORECASE,
)

# Long, detailed messages are instructions, not vague pointers, even if they
# happen to contain a trigger word.
_VAGUE_MAX_WORDS = 25


# Tools that read or act on a repository. Blocked while the gate is armed.
_GATED_TOOLS = frozenset({"read_file", "write_file", "patch", "search_files", "edit_file"})

# terminal is dual-use; only mutating commands are gated (read-only git/ls/cat
# are how the model gathers evidence to disambiguate).
# Read-only commands the model uses to gather disambiguation evidence. A command
# containing a redirection or shell chaining is never treated as read-only, so
# `cat x > y` or `ls && rm z` can't slip through.
_READONLY_TERMINAL_RE = re.compile(
    r"^\s*(git\s+(status|log|diff|show|branch|remote|rev-parse|config\s+--get)|"
    r"ls|ll|cat|pwd|head|tail|find|grep|rg|which|env|date|whoami|stat|wc|file)\b",
    re.IGNORECASE,
)
_SHELL_MUTATION_RE = re.compile(r"[>|&;`$]|\brm\b|\bmv\b|\bcp\b|\btee\b")


BLOCK_MESSAGE = (
    "[work-lane gate] This reads like a continuation, but which project/workspace "
    "it refers to has not been confirmed this session. Before reading or changing "
    "any file, use the `clarify` tool to ask the user which project they mean — "
    "offer the concrete candidates you can actually justify (recent sessions, an "
    "agent job, a source note), with full paths. Do NOT presume a project from old "
    "notes, a directory listing, or the first repo you find. You may still use "
    "clarify, session_search, the vault read tools, and read-only git to gather "
    "those candidates."
)


def is_vague_continuation(user_message: str) -> bool:
    """True when the message points at prior work without naming a target."""
    if not isinstance(user_message, str):
        return False
    text = user_message.strip()
    if not text:
        return False
    if _EXPLICIT_TARGET_RE.search(text):
        return False
    if len(text.split()) > _VAGUE_MAX_WORDS:
        return False
    return bool(_VAGUE_CONTINUATION_RE.search(text))


def should_arm(user_message: str, has_confirmed_lane: bool) -> bool:
    """Arm the gate only for a vague continuation with no known project."""
    if has_confirmed_lane:
        return False
    return is_vague_continuation(user_message)


def _is_readonly_terminal(command: str) -> bool:
    command = command or ""
    if _SHELL_MUTATION_RE.search(command):
        return False
    return bool(_READONLY_TERMINAL_RE.match(command))


def evaluate(function_name: str, function_args: dict) -> str | None:
    """Return a block message if this tool must wait for project confirmation.

    Pure. Caller decides arming/satisfaction; this only classifies the tool.
    Returns None (allow) for everything not repo-touching.
    """
    if function_name in _GATED_TOOLS:
        return BLOCK_MESSAGE
    if function_name == "terminal":
        command = (function_args or {}).get("command", "")
        # No command to inspect -> nothing to gate (fail open).
        if command and not _is_readonly_terminal(command):
            return BLOCK_MESSAGE
    return None


def gate_block_message(agent, function_name: str, function_args: dict) -> str | None:
    """Fail-open wrapper used at the tool-executor seam.

    Returns a block message only when the gate is armed, not yet satisfied, and
    the tool is repo-touching. ANY error -> None (allow), because a gate bug
    must never brick tool execution.
    """
    try:
        if not getattr(agent, "_lane_gate_armed", False):
            return None
        if getattr(agent, "_lane_gate_satisfied", False):
            return None
        return evaluate(function_name, function_args or {})
    except Exception:
        return None
