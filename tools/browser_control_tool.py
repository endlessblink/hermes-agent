"""Agent tools that act inside the tab pinned in the Hermes browser extension.

These are deliberately separate from ``browser_*``. ``browser_*`` drives a
headless/cloud browser — a different browser, with different cookies, that hits
login walls and bot challenges on pages the user is already signed in to. The
tools here run in the user's real tab through the extension's own content
script, so the page state the agent was shown is the page state it acts on.

Contract every mutating tool honours:
  - the command runs in the pinned tab or fails loudly; there is no fallback
  - targets come from a fresh snapshot's element ref, never from guessed text
  - after a side effect the extension reads the DOM back, and that read-back is
    what the tool reports — not merely "the command was accepted"
  - the extension never submits a form; filling and submitting stay separate
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from gateway.browser_control import (
    BROKER,
    BrowserControlError,
    CommandTimeoutError,
    NoBrowserChannelError,
    TabMismatchError,
)
from tools.registry import registry

logger = logging.getLogger(__name__)

TOOLSET = "browser_control"

# Uploads name a file on the Hermes host. Anything outside this allowlist is
# refused before the path ever reaches the extension.
_UPLOAD_ALLOWED_SUFFIXES = {
    ".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md",
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".csv", ".xlsx", ".json",
}

# Bytes travel inline in the command payload, so keep it well under any
# reasonable request ceiling. Résumés and screenshots fit; archives do not.
_UPLOAD_MAX_BYTES = 8 * 1024 * 1024


def channel_available() -> bool:
    """check_fn: only expose these tools while a side panel is connected.

    Keeps the headless-browser story unchanged for every other surface — the
    tools simply are not in the schema unless the extension is there.
    """
    try:
        return bool(BROKER.channel_status().get("connected"))
    except Exception:  # pragma: no cover - defensive
        logger.debug("browser control availability check failed", exc_info=True)
        return False


def _error(exc: Exception) -> str:
    """Turn a broker failure into a precise, non-guessable reason.

    The model must never conclude "the extension is read-only" or retry the
    headless tools; each reason names the specific missing capability.
    """
    if isinstance(exc, NoBrowserChannelError):
        code = "no_browser_channel"
    elif isinstance(exc, TabMismatchError):
        code = "tab_not_pinned"
    elif isinstance(exc, CommandTimeoutError):
        code = "browser_command_timeout"
    else:
        code = "browser_control_failed"
    return json.dumps({
        "ok": False,
        "error": code,
        "message": str(exc),
        "hint": "Do not retry this with browser_* — those open a different browser "
                "session that is not signed in to this page.",
    })


def _run(action: str, params: Dict[str, Any], timeout: float = 30.0) -> str:
    try:
        result = BROKER.submit(action, params, timeout=timeout)
    except BrowserControlError as exc:
        return _error(exc)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("browser control %s failed", action)
        return _error(BrowserControlError(str(exc)))
    return json.dumps(result, default=str)


# ── handlers ──────────────────────────────────────────────────────────


def extension_browser_snapshot(args: Dict[str, Any] | None = None, **_kw: Any) -> str:
    """Return interactive elements of the pinned tab with stable refs."""
    values = args or {}
    return _run("snapshot", {
        "include_hidden": bool(values.get("include_hidden", False)),
        "max_elements": max(1, min(int(values.get("max_elements", 150) or 150), 400)),
    })


def extension_browser_click(args: Dict[str, Any] | None = None, **_kw: Any) -> str:
    values = args or {}
    ref = str(values.get("ref") or "").strip()
    if not ref:
        return json.dumps({"ok": False, "error": "missing_ref",
                           "message": "click requires a `ref` from a fresh snapshot."})
    return _run("click", {"ref": ref})


def extension_browser_type(args: Dict[str, Any] | None = None, **_kw: Any) -> str:
    values = args or {}
    ref = str(values.get("ref") or "").strip()
    if not ref:
        return json.dumps({"ok": False, "error": "missing_ref",
                           "message": "type requires a `ref` from a fresh snapshot."})
    return _run("type", {
        "ref": ref,
        "text": str(values.get("text") or ""),
        "clear": bool(values.get("clear", True)),
    })


def extension_browser_select(args: Dict[str, Any] | None = None, **_kw: Any) -> str:
    values = args or {}
    ref = str(values.get("ref") or "").strip()
    if not ref:
        return json.dumps({"ok": False, "error": "missing_ref",
                           "message": "select requires a `ref` from a fresh snapshot."})
    return _run("select", {"ref": ref, "value": str(values.get("value") or "")})


def extension_browser_scroll(args: Dict[str, Any] | None = None, **_kw: Any) -> str:
    values = args or {}
    direction = str(values.get("direction") or "down").lower()
    if direction not in ("up", "down", "top", "bottom", "element"):
        direction = "down"
    return _run("scroll", {
        "direction": direction,
        "amount": max(1, min(int(values.get("amount", 3) or 3), 20)),
        "ref": str(values.get("ref") or "").strip(),
    })


def extension_browser_upload(args: Dict[str, Any] | None = None, **_kw: Any) -> str:
    """Attach a host file to a file input in the pinned tab.

    The agent must name an explicit path; there is no directory browsing, no
    globbing, and no reading of file contents into the conversation.
    """
    from pathlib import Path

    values = args or {}
    ref = str(values.get("ref") or "").strip()
    raw_path = str(values.get("path") or "").strip()
    if not ref:
        return json.dumps({"ok": False, "error": "missing_ref",
                           "message": "upload requires a `ref` for the file input."})
    if not raw_path:
        return json.dumps({"ok": False, "error": "missing_path",
                           "message": "upload requires an explicit `path`; it will not pick a file for you."})
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        return json.dumps({"ok": False, "error": "path_not_absolute",
                           "message": "upload requires an absolute path."})
    if not path.is_file():
        return json.dumps({"ok": False, "error": "file_not_found",
                           "message": f"No file at {path}."})
    if path.suffix.lower() not in _UPLOAD_ALLOWED_SUFFIXES:
        return json.dumps({
            "ok": False, "error": "file_type_not_allowed",
            "message": f"{path.suffix or 'files with no extension'} cannot be uploaded by the agent.",
        })
    size = path.stat().st_size
    if size > _UPLOAD_MAX_BYTES:
        return json.dumps({
            "ok": False, "error": "file_too_large",
            "message": f"{path.name} is {size // 1024}KB; the limit is {_UPLOAD_MAX_BYTES // 1024}KB.",
        })
    # The bytes cross to the extension here rather than the extension reading
    # the disk: file access stays on the Hermes side where the path was
    # validated, and the content never enters the conversation or the logs.
    import base64
    import mimetypes

    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return _run("upload", {
        "ref": ref,
        "name": path.name,
        "mime": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "content_base64": payload,
    }, timeout=60.0)


def extension_browser_status(args: Dict[str, Any] | None = None, **_kw: Any) -> str:
    """Report whether a pinned tab is reachable, and which one."""
    return json.dumps(BROKER.channel_status(), default=str)


# ── schemas ───────────────────────────────────────────────────────────

_REF = {
    "type": "string",
    "description": "Element ref from the most recent extension_browser_snapshot. "
                   "Refs go stale when the page re-renders — re-snapshot on a stale_ref error.",
}

SCHEMAS: Dict[str, Dict[str, Any]] = {
    "extension_browser_snapshot": {
        "name": "extension_browser_snapshot",
        "description": (
            "List the interactive elements (inputs, buttons, links, selects, file "
            "inputs) of the tab pinned in the Hermes browser extension, each with a "
            "stable ref to act on. Use this before any click/type/select. This reads "
            "the user's real signed-in tab, not a headless browser."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "include_hidden": {"type": "boolean", "description": "Include off-screen elements. Default false."},
                "max_elements": {"type": "integer", "description": "Cap on returned elements (default 150, max 400)."},
            },
        },
    },
    "extension_browser_click": {
        "name": "extension_browser_click",
        "description": "Click an element in the pinned tab by ref. Never used to submit a form.",
        "parameters": {"type": "object", "properties": {"ref": _REF}, "required": ["ref"]},
    },
    "extension_browser_type": {
        "name": "extension_browser_type",
        "description": (
            "Type text into an input or textarea in the pinned tab by ref. Fires the "
            "input/change events React and Vue need, then reads the value back."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ref": _REF,
                "text": {"type": "string", "description": "Text to enter."},
                "clear": {"type": "boolean", "description": "Clear the field first. Default true."},
            },
            "required": ["ref", "text"],
        },
    },
    "extension_browser_select": {
        "name": "extension_browser_select",
        "description": "Choose an option in a native <select> or a custom combobox by ref, by visible label or value.",
        "parameters": {
            "type": "object",
            "properties": {"ref": _REF, "value": {"type": "string", "description": "Option label or value."}},
            "required": ["ref", "value"],
        },
    },
    "extension_browser_scroll": {
        "name": "extension_browser_scroll",
        "description": "Scroll the pinned tab, or scroll a specific element into view.",
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down", "top", "bottom", "element"]},
                "amount": {"type": "integer", "description": "Viewport fractions to scroll (default 3)."},
                "ref": {"type": "string", "description": "Required when direction is 'element'."},
            },
        },
    },
    "extension_browser_upload": {
        "name": "extension_browser_upload",
        "description": (
            "Attach a file from this machine to a file input in the pinned tab. "
            "Requires an explicit absolute path; the agent cannot browse for files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ref": _REF,
                "path": {"type": "string", "description": "Absolute path of the file to attach."},
            },
            "required": ["ref", "path"],
        },
    },
    "extension_browser_status": {
        "name": "extension_browser_status",
        "description": "Check whether the extension side panel is connected and which tab is pinned.",
        "parameters": {"type": "object", "properties": {}},
    },
}

_HANDLERS = {
    "extension_browser_snapshot": extension_browser_snapshot,
    "extension_browser_click": extension_browser_click,
    "extension_browser_type": extension_browser_type,
    "extension_browser_select": extension_browser_select,
    "extension_browser_scroll": extension_browser_scroll,
    "extension_browser_upload": extension_browser_upload,
    "extension_browser_status": extension_browser_status,
}

for _name, _schema in SCHEMAS.items():
    registry.register(
        name=_name,
        toolset=TOOLSET,
        schema=_schema,
        handler=_HANDLERS[_name],
        check_fn=channel_available,
        requires_env=[],
        description=_schema["description"],
    )


__all__ = ["TOOLSET", "SCHEMAS", "channel_available", *_HANDLERS.keys()]
