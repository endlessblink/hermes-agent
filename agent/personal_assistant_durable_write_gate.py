"""Prevent generic tools from bypassing Personal Assistant capture approval."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.personal_assistant_obsidian import NOTE_PATH


_FILE_MUTATION_TOOLS = frozenset(
    {
        "delete_file",
        "delete_path",
        "move_file",
        "patch",
        "write_file",
    }
)
_PROTECTED_BASENAME = NOTE_PATH.rsplit("/", 1)[-1].casefold()
_PROTECTED_SUFFIX = NOTE_PATH.replace("\\", "/").casefold()
_OBSIDIAN_MARKERS = ("obsidian_synced", "main vult", ".obsidian")


def _string_values(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for nested in value.values():
            yield from _string_values(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _string_values(nested)


def _targets_personal_assistant_note(args: Mapping[str, Any] | None) -> bool:
    for value in _string_values(args or {}):
        normalized = value.replace("\\", "/").casefold()
        if _PROTECTED_SUFFIX in normalized or _PROTECTED_BASENAME in normalized:
            return True
    return False


def _targets_obsidian_vault(args: Mapping[str, Any] | None) -> bool:
    return any(
        marker in value.replace("\\", "/").casefold()
        for value in _string_values(args or {})
        for marker in _OBSIDIAN_MARKERS
    )


def durable_personal_assistant_write_gate_message(
    function_name: str,
    args: Mapping[str, Any] | None,
    *,
    lifeboat_mode: bool = False,
) -> str | None:
    """Return a model-facing error for direct writes to the durable PA note."""

    if function_name not in _FILE_MUTATION_TOOLS:
        return None
    if lifeboat_mode and _targets_obsidian_vault(args):
        return (
            "Direct writes to the Obsidian vault are blocked in the Life-Boat lane. "
            "First show the user the exact short summary and ask for explicit approval; "
            "do not patch a journal or personal pattern note during emotional processing."
        )
    if not _targets_personal_assistant_note(args):
        return None
    return (
        "Direct writes to the Personal Assistant durable knowledge note are blocked. "
        "Use personal_assistant_propose_capture to present the narrow correction, "
        "wait for the user's explicit approval, then use the dedicated approved "
        "capture flow. Do not claim the preference was saved before approval and "
        "authoritative readback."
    )
