"""Standalone, user-installed Notion to FlowState activation bridge.

Copy this directory to ``$HERMES_HOME/plugins/notion-flowstate-bridge`` and
enable ``notion-flowstate-bridge`` in Hermes' plugin configuration.  Remote
mutations are always previewed and cryptographically bound before apply.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

from .bridge import Bridge, BridgeConfig, BridgeError

_bridge: Bridge | None = None
_bridge_lock = threading.Lock()


def _runtime_config() -> BridgeConfig:
    settings: dict[str, Any] = {}
    try:
        from hermes_cli.config import load_config

        root = load_config() or {}
        settings = (
            ((root.get("plugins") or {}).get("entries") or {})
            .get("notion-flowstate-bridge", {})
            .get("config", {})
        )
    except Exception:
        settings = {}
    hermes_home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    writable = settings.get("notion_writable_properties") or []
    if not isinstance(writable, (list, tuple)):
        writable = []
    return BridgeConfig(
        notion_token=os.environ.get("NOTION_TOKEN", ""),
        notion_data_source_id=str(settings.get("notion_data_source_id") or ""),
        notion_idempotency_property=str(
            settings.get("notion_idempotency_property") or "Hermes operation ID"
        ),
        notion_writable_properties=tuple(
            str(name).strip() for name in writable if str(name).strip()
        ),
        flowstate_base_url=str(
            settings.get("flowstate_base_url") or "http://127.0.0.1:8765"
        ),
        flowstate_token=os.environ.get("FLOWSTATE_TOKEN", ""),
        state_path=Path(
            settings.get("state_path")
            or hermes_home / "state" / "notion-flowstate-bridge.sqlite3"
        ),
        preview_ttl_seconds=int(settings.get("preview_ttl_seconds") or 900),
    )


def _get_bridge() -> Bridge:
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            _bridge = Bridge(_runtime_config())
        return _bridge


def _available(profile_name: str) -> bool:
    if profile_name != "office-work" or not os.environ.get("NOTION_TOKEN"):
        return False
    try:
        _runtime_config()
    except (BridgeError, TypeError, ValueError):
        return False
    return True


def _handler(method: str) -> Callable[..., str]:
    def call(args: dict[str, Any], **_: Any) -> str:
        try:
            result = getattr(_get_bridge(), method)(args)
            return json.dumps({"ok": True, **result}, ensure_ascii=False, separators=(",", ":"))
        except BridgeError as exc:
            return json.dumps(
                {"ok": False, "error": {"code": exc.code, "message": exc.public_message}},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        except Exception:
            return json.dumps(
                {"ok": False, "error": {"code": "internal_error", "message": "Bridge request failed"}},
                separators=(",", ":"),
            )

    return call


_SCHEMAS = {
    "notion_data_source_schema": {
        "name": "notion_data_source_schema",
        "description": "Read a Notion data-source schema without changing it.",
        "parameters": {
            "type": "object",
            "properties": {"data_source_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "notion_data_source_list": {
        "name": "notion_data_source_list",
        "description": "List pages in a Notion data source with a bounded filter and page size.",
        "parameters": {
            "type": "object",
            "properties": {
                "data_source_id": {"type": "string"},
                "filter": {"type": "object"},
                "sorts": {"type": "array", "maxItems": 10},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
                "start_cursor": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    "notion_page_get": {
        "name": "notion_page_get",
        "description": "Read one exact Notion page.",
        "parameters": {
            "type": "object",
            "properties": {"page_id": {"type": "string"}},
            "required": ["page_id"],
            "additionalProperties": False,
        },
    },
    "notion_mutation": {
        "name": "notion_mutation",
        "description": "Preview or apply a version-bound Notion task create, property update, status change, or archive. Defaults to preview.",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"},
                "operation_id": {"type": "string"},
                "action": {
                    "type": "string",
                    "enum": ["create_task", "update_properties", "set_status", "archive_task"],
                },
                "data_source_id": {"type": "string"},
                "page_id": {"type": "string"},
                "properties": {"type": "object"},
                "status_property": {"type": "string"},
                "status_name": {"type": "string"},
                "preview_digest": {"type": "string"},
            },
            "required": ["operation_id", "action"],
            "oneOf": [
                {
                    "properties": {"action": {"const": "create_task"}},
                    "required": ["properties"],
                },
                {
                    "properties": {"action": {"const": "update_properties"}},
                    "required": ["page_id", "properties"],
                },
                {
                    "properties": {"action": {"const": "set_status"}},
                    "required": ["page_id", "status_property", "status_name"],
                },
                {
                    "properties": {"action": {"const": "archive_task"}},
                    "required": ["page_id"],
                },
            ],
            "additionalProperties": False,
        },
    },
    "notion_flowstate_activate": {
        "name": "notion_flowstate_activate",
        "description": "Preview or apply activation of an exact Notion page in canonical FlowState.",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"},
                "operation_id": {"type": "string"},
                "data_source_id": {"type": "string"},
                "page_id": {"type": "string"},
                "task": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "priority": {"type": ["string", "null"], "enum": ["low", "medium", "high", None]},
                        "dueDate": {"type": ["string", "null"]},
                        "projectId": {"type": ["string", "null"]},
                    },
                    "required": ["title"],
                    "additionalProperties": False,
                },
                "work_block": {"type": "object"},
                "preview_digest": {"type": "string"},
            },
            "required": ["operation_id", "page_id", "task"],
            "additionalProperties": False,
        },
    },
}


def register(ctx: Any) -> None:
    """Register the five service-gated bridge tools with Hermes."""
    profile_name = getattr(ctx, "profile_name", "default")
    methods = {
        "notion_data_source_schema": "read_schema",
        "notion_data_source_list": "list_pages",
        "notion_page_get": "read_page",
        "notion_mutation": "mutate_notion",
        "notion_flowstate_activate": "activate",
    }
    for name, method in methods.items():
        ctx.register_tool(
            name=name,
            toolset="notion_flowstate_bridge",
            schema=_SCHEMAS[name],
            handler=_handler(method),
            check_fn=lambda profile_name=profile_name: _available(profile_name),
            description=_SCHEMAS[name]["description"],
            emoji="🔗",
        )


__all__ = ["Bridge", "BridgeConfig", "BridgeError", "register"]
