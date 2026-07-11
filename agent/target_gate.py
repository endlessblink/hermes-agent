"""Pre-action gate: don't generate/animate on an unconfirmed visual target.

The film-maker failure: the user is looking at a specific frame and says "animate
this", but the backend only receives text — so the model resolves "this" from
chat history or recalled memory and generates/animates the WRONG artifact. This
gate makes it confirm the target first (or bind to the app-provided active
target) before running an image/video generation tool.

It arms only when BOTH are true:
  * the user's message is a bare visual deictic ("animate this", "generate it",
    "תנפיש את זה") — no named target, and
  * there is no confirmed active target this turn (no attached image, no
    app-provided active_target that the user can be pointing at).

When armed, generation/animation tools are refused with a message telling the
model to confirm which frame/image via `clarify` (or bind to the active target)
instead of guessing. `clarify` satisfies the gate for the rest of the turn, so
it can never loop.

Reliability contract: pure, and every entry point fails OPEN — a bug here must
never block a tool. Call sites wrap it in try/except returning "don't block".
Mirror of ``agent/lane_gate.py`` for visual generation.
"""

from __future__ import annotations

import re


# A bare visual deictic points at an on-screen artifact without naming it.
# Bilingual (English + Hebrew), deliberately narrow so a specific instruction
# like "generate a 4k poster of a red car" does NOT match.
_VISUAL_DEICTIC_RE = re.compile(
    r"""
    \b(
        animate | re-?animate | generate | regenerate | render | re-?render |
        make | create | produce | upscale | redo | try\s+again
    )\b
    [^.!?\n]{0,40}?
    \b(this|it|that|these|those|the\s+(?:frame|image|shot|clip|picture|render|generation|one))\b
    |
    (?:תנפיש | תנפישי | הנפש | הנפישי | תייצר | תייצרי | צור | תעשה | תעשי | תרנדר)
    \s* (?:את\s*)? (?:זה | זו | אותו | אותה | הפריים | התמונה | השוט)
    """,
    re.IGNORECASE | re.VERBOSE | re.UNICODE,
)

# Names a concrete visual target the user pointed at — a file/path, a timecode,
# or an explicit "the <name> frame/shot". If present, it isn't a bare deictic.
_EXPLICIT_VISUAL_TARGET_RE = re.compile(
    r"(?:@image:|@file:|data:image/)"                       # an attached/inlined image ref
    r"|(?:/[\w.\-]+/[\w./\-]+\.(?:png|jpg|jpeg|webp|mp4|mov|gif))"  # an explicit media path
    r"|(?:\b\d{1,2}:\d{2}\b)"                               # a timecode like 0:05
    r"|(?:\b(?:frame|shot|take)\s+[\w\-]+\b)",              # "shot 13", "frame jane"
    re.IGNORECASE,
)

# Long, detailed messages are instructions, not bare pointers.
_VISUAL_MAX_WORDS = 30


# Tools that create/animate a visual artifact. Blocked while the gate is armed.
_GENERATION_TOOLS = frozenset(
    {"image_generate", "video_generate", "xai_video_edit", "xai_video_extend"}
)


def _is_magnific_creation(function_name: str) -> bool:
    """Magnific MCP tools that START a creation (upload/animate), not read-only ones."""
    n = (function_name or "").lower()
    if "magnific" not in n:
        return False
    return "creations" in n and ("upload" in n or "animate" in n or "generate" in n)


BLOCK_MESSAGE = (
    "[target gate] This asks to generate/animate 'this' but no specific target "
    "frame/image is confirmed for this turn. Do NOT guess the target from earlier "
    "generations, chat history, or recalled memory. Use `clarify` to ask the user "
    "which exact frame/image they mean (offer the concrete candidates you can "
    "justify — the frame in the preview, an attached image, a named shot with its "
    "path), or bind to a confirmed active target. Only generate once the target is "
    "unambiguous."
)


def is_visual_deictic(user_message: str) -> bool:
    """True when the message asks to generate/animate a target it doesn't name."""
    if not isinstance(user_message, str):
        return False
    text = user_message.strip()
    if not text:
        return False
    if _EXPLICIT_VISUAL_TARGET_RE.search(text):
        return False
    if len(text.split()) > _VISUAL_MAX_WORDS:
        return False
    return bool(_VISUAL_DEICTIC_RE.search(text))


def should_arm(user_message: str, has_active_target: bool) -> bool:
    """Arm only for a bare visual deictic with no confirmed target this turn."""
    if has_active_target:
        return False
    return is_visual_deictic(user_message)


def evaluate(function_name: str, function_args: dict) -> str | None:
    """Return a block message if this tool generates/animates. Pure; caller
    decides arming/satisfaction. None (allow) for everything else."""
    if function_name in _GENERATION_TOOLS or _is_magnific_creation(function_name):
        return BLOCK_MESSAGE
    return None


def gate_block_message(agent, function_name: str, function_args: dict) -> str | None:
    """Fail-open wrapper for the tool-executor seam. Blocks only when the gate is
    armed, not yet satisfied, and the tool is generative. ANY error -> None."""
    try:
        if not getattr(agent, "_target_gate_armed", False):
            return None
        if getattr(agent, "_target_gate_satisfied", False):
            return None
        return evaluate(function_name, function_args or {})
    except Exception:
        return None
