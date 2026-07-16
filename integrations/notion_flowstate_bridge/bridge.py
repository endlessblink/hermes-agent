"""Safe Notion mutation and FlowState activation bridge.

Only Python's standard library is used.  Tokens stay exclusively in request
headers and are never included in persisted previews, receipts, exceptions,
or tool output.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

MAX_INPUT_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
MAX_IDENTIFIER = 256
MAX_OPERATION_ID = 200
SUPPORTED_NOTION_PROPERTY_TYPES = {
    "title",
    "rich_text",
    "number",
    "select",
    "multi_select",
    "status",
    "date",
    "people",
    "checkbox",
    "url",
    "email",
    "phone_number",
    "relation",
}


class BridgeError(RuntimeError):
    def __init__(self, code: str, public_message: str):
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message


@dataclass(frozen=True)
class BridgeConfig:
    notion_token: str = field(repr=False)
    notion_data_source_id: str
    notion_idempotency_property: str
    notion_writable_properties: tuple[str, ...]
    flowstate_base_url: str
    state_path: Path
    flowstate_token: str = field(default="", repr=False)
    notion_base_url: str = "https://api.notion.com/v1"
    notion_version: str = "2026-03-11"
    preview_ttl_seconds: int = 900
    timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        if not self.notion_token:
            raise BridgeError("not_configured", "Notion authentication is not configured")
        if not self.notion_data_source_id.strip():
            raise BridgeError("not_configured", "A Notion data source must be configured")
        if not self.notion_idempotency_property.strip():
            raise BridgeError(
                "not_configured", "A Notion rich_text idempotency property must be configured"
            )
        if not self.notion_writable_properties or any(
            not isinstance(name, str) or not name.strip()
            for name in self.notion_writable_properties
        ):
            raise BridgeError(
                "not_configured", "At least one writable Notion property must be configured"
            )
        if not 30 <= int(self.preview_ttl_seconds) <= 3600:
            raise BridgeError("invalid_config", "Preview lifetime must be between 30 and 3600 seconds")
        if not self.flowstate_base_url.startswith(("http://", "https://")):
            raise BridgeError("invalid_config", "FlowState URL must use HTTP or HTTPS")


def _canonical(value: Any) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise BridgeError("invalid_input", "Input must be valid JSON data") from exc
    if len(encoded.encode("utf-8")) > MAX_INPUT_BYTES:
        raise BridgeError("input_too_large", "Request exceeds the bridge input limit")
    return encoded


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _required_text(value: Any, name: str, maximum: int = MAX_IDENTIFIER) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BridgeError("invalid_input", f"{name} is required")
    result = value.strip()
    if len(result) > maximum or any(ord(char) < 32 for char in result):
        raise BridgeError("invalid_input", f"{name} is invalid")
    return result


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BridgeError("invalid_input", f"{name} must be an object")
    _canonical(value)
    return value


def _parse_time(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError as exc:
            raise BridgeError("invalid_response", "Remote preview returned an invalid expiry") from exc
    raise BridgeError("invalid_response", "Remote preview did not return an expiry")


class JsonTransport:
    def __init__(self, *, timeout: float = 15.0, opener: Callable[..., Any] | None = None):
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = _canonical(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            code = "remote_auth" if exc.code in {401, 403} else "remote_conflict" if exc.code == 409 else "remote_error"
            raise BridgeError(code, f"Remote service rejected the request (HTTP {exc.code})") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise BridgeError("remote_unavailable", "Remote service is unavailable") from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise BridgeError("response_too_large", "Remote response exceeded the bridge limit")
        try:
            result = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BridgeError("invalid_response", "Remote service returned invalid JSON") from None
        if not isinstance(result, dict):
            raise BridgeError("invalid_response", "Remote service returned an invalid object")
        if len(_canonical(result).encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise BridgeError("response_too_large", "Remote response exceeded the tool output limit")
        return result


class ReceiptStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS previews (
                    operation_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    preview_digest TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (operation_id, kind)
                );
                CREATE TABLE IF NOT EXISTS receipts (
                    operation_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    status TEXT NOT NULL,
                    remote_id TEXT,
                    receipt_json TEXT,
                    claimed_at REAL,
                    verified_at REAL,
                    PRIMARY KEY (operation_id, kind)
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def save_preview(
        self,
        operation_id: str,
        kind: str,
        request: dict[str, Any],
        preview_digest: str,
        expires_at: float,
        now: float,
    ) -> None:
        request_json = _canonical(request)
        request_digest = _digest(request)
        with self._connect() as db:
            existing_receipt = db.execute(
                "SELECT request_digest FROM receipts WHERE operation_id=? AND kind=?",
                (operation_id, kind),
            ).fetchone()
            existing = db.execute(
                "SELECT request_digest, preview_digest FROM previews WHERE operation_id=? AND kind=?",
                (operation_id, kind),
            ).fetchone()
            if existing_receipt and existing_receipt["request_digest"] != request_digest:
                raise BridgeError("operation_conflict", "Operation ID is already bound to different input")
            if existing and (
                existing["request_digest"] != request_digest
                or existing["preview_digest"] != preview_digest
            ):
                raise BridgeError("operation_conflict", "Operation ID is already bound to a different preview")
            db.execute(
                """INSERT INTO previews
                   (operation_id,kind,request_digest,preview_digest,request_json,expires_at,created_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(operation_id,kind) DO UPDATE SET expires_at=excluded.expires_at""",
                (operation_id, kind, request_digest, preview_digest, request_json, expires_at, now),
            )

    def require_preview(
        self,
        operation_id: str,
        kind: str,
        request: dict[str, Any],
        preview_digest: str,
        now: float,
        *,
        allow_expired: bool = False,
    ) -> sqlite3.Row:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM previews WHERE operation_id=? AND kind=?",
                (operation_id, kind),
            ).fetchone()
        if row is None:
            raise BridgeError("preview_required", "A matching preview is required before apply")
        if row["request_digest"] != _digest(request) or row["preview_digest"] != preview_digest:
            raise BridgeError("preview_mismatch", "Apply input does not match the approved preview")
        if float(row["expires_at"]) <= now and not allow_expired:
            raise BridgeError("preview_expired", "The approved preview has expired")
        return row

    def resolve_preview(
        self,
        operation_id: str,
        kind: str,
        intent: dict[str, Any],
        preview_digest: str,
    ) -> tuple[sqlite3.Row, dict[str, Any]]:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM previews WHERE operation_id=? AND kind=?",
                (operation_id, kind),
            ).fetchone()
        if row is None:
            raise BridgeError("preview_required", "A matching preview is required before apply")
        if row["preview_digest"] != preview_digest:
            raise BridgeError("preview_mismatch", "Apply input does not match the approved preview")
        try:
            request = json.loads(row["request_json"])
        except (TypeError, json.JSONDecodeError):
            raise BridgeError("invalid_state", "Stored preview is invalid") from None
        if not isinstance(request, dict) or request.get("intent_digest") != _digest(intent):
            raise BridgeError("preview_mismatch", "Apply input does not match the approved preview")
        if row["request_digest"] != _digest(request):
            raise BridgeError("invalid_state", "Stored preview failed its integrity check")
        return row, request

    def has_stale_claim(
        self, operation_id: str, kind: str, request: dict[str, Any], now: float
    ) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT request_digest,status,claimed_at FROM receipts WHERE operation_id=? AND kind=?",
                (operation_id, kind),
            ).fetchone()
        if row is None:
            return False
        if row["request_digest"] != _digest(request):
            raise BridgeError("operation_conflict", "Operation ID is already bound to different input")
        return (
            row["status"] == "applying"
            and float(row["claimed_at"] or 0) <= now - 60
        )

    def receipt(self, operation_id: str, kind: str, request: dict[str, Any]) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM receipts WHERE operation_id=? AND kind=?",
                (operation_id, kind),
            ).fetchone()
        if row is None:
            return None
        if row["request_digest"] != _digest(request):
            raise BridgeError("operation_conflict", "Operation ID is already bound to different input")
        if row["status"] == "verified" and row["receipt_json"]:
            value = json.loads(row["receipt_json"])
            value["duplicate"] = True
            return value
        return None

    def claim(
        self, operation_id: str, kind: str, request: dict[str, Any], now: float
    ) -> dict[str, Any] | None:
        request_digest = _digest(request)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM receipts WHERE operation_id=? AND kind=?",
                (operation_id, kind),
            ).fetchone()
            if row and row["request_digest"] != request_digest:
                raise BridgeError("operation_conflict", "Operation ID is already bound to different input")
            if row and row["status"] == "verified" and row["receipt_json"]:
                receipt = json.loads(row["receipt_json"])
                receipt["duplicate"] = True
                return receipt
            if row and row["status"] == "applying" and float(row["claimed_at"] or 0) > now - 60:
                raise BridgeError("operation_in_progress", "The same operation is already being applied")
            db.execute(
                """INSERT INTO receipts(operation_id,kind,request_digest,status,claimed_at)
                   VALUES (?,?,?,'applying',?)
                   ON CONFLICT(operation_id,kind) DO UPDATE
                   SET status='applying', claimed_at=excluded.claimed_at""",
                (operation_id, kind, request_digest, now),
            )
        return None

    def abandon(self, operation_id: str, kind: str) -> None:
        with self._connect() as db:
            db.execute(
                "DELETE FROM receipts WHERE operation_id=? AND kind=? AND status='applying'",
                (operation_id, kind),
            )

    def verify(
        self,
        operation_id: str,
        kind: str,
        request: dict[str, Any],
        remote_id: str,
        receipt: dict[str, Any],
        now: float,
    ) -> dict[str, Any]:
        receipt_json = _canonical(receipt)
        with self._connect() as db:
            db.execute(
                """UPDATE receipts SET status='verified',remote_id=?,receipt_json=?,verified_at=?
                   WHERE operation_id=? AND kind=? AND request_digest=?""",
                (remote_id, receipt_json, now, operation_id, kind, _digest(request)),
            )
        return receipt


class Bridge:
    def __init__(
        self,
        config: BridgeConfig,
        *,
        transport: JsonTransport | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.config = config
        self.transport = transport or JsonTransport(timeout=config.timeout_seconds)
        self.clock = clock
        self.store = ReceiptStore(config.state_path)

    def _notion_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.notion_token}",
            "Notion-Version": self.config.notion_version,
            "Content-Type": "application/json",
        }

    def _flowstate_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.flowstate_token:
            headers["Authorization"] = f"Bearer {self.config.flowstate_token}"
        return headers

    def _notion(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.transport.request(
            method,
            self.config.notion_base_url.rstrip("/") + path,
            headers=self._notion_headers(),
            payload=payload,
        )

    def _data_source_id(self, args: Mapping[str, Any]) -> str:
        configured = _required_text(self.config.notion_data_source_id, "configured data_source_id")
        requested = args.get("data_source_id")
        if requested is not None and _required_text(requested, "data_source_id") != configured:
            raise BridgeError("scope_violation", "Notion data source is outside the configured scope")
        return configured

    def read_schema(self, args: dict[str, Any]) -> dict[str, Any]:
        data_source_id = self._data_source_id(args)
        return {"data_source": self._notion("GET", f"/data_sources/{urllib.parse.quote(data_source_id)}")}

    def list_pages(self, args: dict[str, Any]) -> dict[str, Any]:
        data_source_id = self._data_source_id(args)
        try:
            page_size = int(args.get("page_size", 50))
        except (TypeError, ValueError):
            raise BridgeError("invalid_input", "page_size must be an integer") from None
        if not 1 <= page_size <= 100:
            raise BridgeError("invalid_input", "page_size must be between 1 and 100")
        payload: dict[str, Any] = {"page_size": page_size}
        for key in ("filter", "sorts"):
            if key in args:
                payload[key] = args[key]
        if args.get("start_cursor"):
            payload["start_cursor"] = _required_text(args["start_cursor"], "start_cursor")
        _canonical(payload)
        return {
            "data_source_id": data_source_id,
            "query": self._notion(
                "POST", f"/data_sources/{urllib.parse.quote(data_source_id)}/query", payload
            ),
        }

    def read_page(self, args: dict[str, Any]) -> dict[str, Any]:
        page_id = _required_text(args.get("page_id"), "page_id")
        page = self._notion("GET", f"/pages/{urllib.parse.quote(page_id)}")
        self._verify_page_identity(page, page_id, self._data_source_id(args))
        return {"page": page}

    def _schema_binding(
        self,
        data_source_id: str,
        properties: Mapping[str, Any],
        *,
        include_idempotency: bool = False,
        validate_values: bool = True,
    ) -> dict[str, Any]:
        schema = self._notion("GET", f"/data_sources/{urllib.parse.quote(data_source_id)}")
        if schema.get("id") != data_source_id:
            raise BridgeError("provenance_mismatch", "Notion returned a different data source")
        available = schema.get("properties")
        if not isinstance(available, dict):
            raise BridgeError("invalid_schema", "Notion data source omitted its property schema")
        names = set(properties)
        if include_idempotency:
            names.add(self.config.notion_idempotency_property)
        binding: dict[str, Any] = {}
        for name in sorted(names):
            definition = available.get(name)
            if not isinstance(definition, dict) or not isinstance(definition.get("type"), str):
                raise BridgeError("invalid_schema", f"Notion property schema is missing for {name}")
            binding[name] = definition
        if include_idempotency and binding[self.config.notion_idempotency_property].get("type") != "rich_text":
            raise BridgeError(
                "invalid_schema", "Configured Notion idempotency property must be rich_text"
            )
        if validate_values:
            for name, value in properties.items():
                expected_type = binding[name]["type"]
                if expected_type not in SUPPORTED_NOTION_PROPERTY_TYPES:
                    raise BridgeError(
                        "unsupported_property_type",
                        f"Notion property {name} cannot be verified safely",
                    )
                if not isinstance(value, dict) or expected_type not in value:
                    raise BridgeError(
                        "invalid_property_type",
                        f"Notion property {name} must use the configured {expected_type} type",
                    )
                if expected_type == "status":
                    selected = value.get("status")
                    selected_name = selected.get("name") if isinstance(selected, dict) else None
                    status_schema = binding[name].get("status")
                    options = status_schema.get("options") if isinstance(status_schema, dict) else None
                    if isinstance(options, list) and selected_name not in {
                        option.get("name") for option in options if isinstance(option, dict)
                    }:
                        raise BridgeError(
                            "invalid_property_value",
                            f"Notion status {selected_name!r} does not exist for {name}",
                        )
        _canonical(binding)
        return binding

    def _create_matches(self, data_source_id: str, operation_id: str) -> list[dict[str, Any]]:
        result = self._notion(
            "POST",
            f"/data_sources/{urllib.parse.quote(data_source_id)}/query",
            {
                "page_size": 2,
                "filter": {
                    "property": self.config.notion_idempotency_property,
                    "rich_text": {"equals": operation_id},
                },
            },
        )
        matches = result.get("results", [])
        if not isinstance(matches, list) or any(not isinstance(item, dict) for item in matches):
            raise BridgeError("invalid_response", "Notion query returned invalid results")
        if len(matches) > 1:
            raise BridgeError("duplicate_remote", "Multiple Notion pages share this operation ID")
        return matches

    @staticmethod
    def _normalize_property(value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if "title" in value or "rich_text" in value:
            kind = "title" if "title" in value else "rich_text"
            items = value.get(kind)
            if not isinstance(items, list):
                return {kind: items}
            text = []
            for item in items:
                if not isinstance(item, dict):
                    text.append(item)
                    continue
                content = ((item.get("text") or {}).get("content"))
                text.append(content if isinstance(content, str) else item.get("plain_text"))
            return {kind: text}
        for kind in ("select", "status"):
            if kind in value:
                selected = value.get(kind)
                return {kind: selected.get("name") if isinstance(selected, dict) else selected}
        if "multi_select" in value:
            selected = value.get("multi_select")
            if isinstance(selected, list):
                return {
                    "multi_select": sorted(
                        item.get("name")
                        for item in selected
                        if isinstance(item, dict) and isinstance(item.get("name"), str)
                    )
                }
            return {"multi_select": selected}
        if "relation" in value:
            related = value.get("relation")
            if isinstance(related, list):
                return {
                    "relation": sorted(
                        item.get("id")
                        for item in related
                        if isinstance(item, dict) and isinstance(item.get("id"), str)
                    )
                }
            return {"relation": related}
        if "people" in value:
            people = value.get("people")
            if isinstance(people, list):
                return {
                    "people": sorted(
                        item.get("id")
                        for item in people
                        if isinstance(item, dict) and isinstance(item.get("id"), str)
                    )
                }
            return {"people": people}
        if "date" in value:
            date = value.get("date")
            if not isinstance(date, dict):
                return {"date": date}
            return {
                "date": {
                    "start": date.get("start"),
                    "end": date.get("end"),
                    "time_zone": date.get("time_zone"),
                }
            }
        for kind in (
            "number", "checkbox", "url", "email", "phone_number"
        ):
            if kind in value:
                return {kind: value[kind]}
        return value

    @classmethod
    def _property_projection(cls, properties: Mapping[str, Any]) -> dict[str, Any]:
        projection: dict[str, Any] = {}
        for name, value in properties.items():
            projection[name] = cls._normalize_property(value)
        return projection

    def _properties_match(self, page: dict[str, Any], expected: Mapping[str, Any]) -> bool:
        actual = page.get("properties")
        if not isinstance(actual, dict):
            raise BridgeError("verification_failed", "Notion read-back omitted page properties")
        return self._property_projection(
            {key: actual.get(key) for key in expected}
        ) == self._property_projection(expected)

    def _verify_properties(self, page: dict[str, Any], expected: Mapping[str, Any]) -> None:
        if not self._properties_match(page, expected):
            raise BridgeError("verification_failed", "Notion read-back did not match the requested properties")

    def _verify_page_source(self, page: Mapping[str, Any], data_source_id: str) -> None:
        actual = self._page_data_source(page)
        if actual != data_source_id:
            raise BridgeError("provenance_mismatch", "Notion page belongs to a different data source")

    def _verify_page_identity(
        self, page: Mapping[str, Any], page_id: str, data_source_id: str
    ) -> None:
        if page.get("id") != page_id:
            raise BridgeError("provenance_mismatch", "Notion returned a different page")
        self._verify_page_source(page, data_source_id)

    def _notion_request(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        operation_id = _required_text(args.get("operation_id"), "operation_id", MAX_OPERATION_ID)
        aliases = {"create": "create_task", "property_update": "update_properties"}
        action = aliases.get(args.get("action"), args.get("action"))
        if action not in {"create_task", "update_properties", "set_status", "archive_task"}:
            raise BridgeError(
                "invalid_input",
                "action must be create_task, update_properties, set_status, or archive_task",
            )
        properties: dict[str, Any]
        if action in {"create_task", "update_properties"}:
            properties = dict(_object(args.get("properties"), "properties"))
            if not properties:
                raise BridgeError("invalid_input", "properties must not be empty")
            if args.get("status_property") is not None or args.get("status_name") is not None:
                raise BridgeError("invalid_input", "status fields are only valid for set_status")
        elif action == "set_status":
            if args.get("properties") is not None:
                raise BridgeError("invalid_input", "properties are not valid for set_status")
            status_property = _required_text(args.get("status_property"), "status_property")
            status_name = _required_text(args.get("status_name"), "status_name")
            properties = {status_property: {"status": {"name": status_name}}}
        else:
            if any(args.get(name) is not None for name in ("properties", "status_property", "status_name")):
                raise BridgeError("invalid_input", "archive_task does not accept property changes")
            properties = {}
        if set(properties) - set(self.config.notion_writable_properties):
            raise BridgeError("scope_violation", "Notion properties are outside the writable allowlist")
        data_source_id = self._data_source_id(args)
        intent: dict[str, Any] = {
            "operation_id": operation_id,
            "action": action,
            "data_source_id": data_source_id,
        }
        if properties:
            intent["properties"] = properties
        if action == "set_status":
            intent["status_property"] = status_property
            intent["status_name"] = status_name
        if action != "create_task":
            intent["page_id"] = _required_text(args.get("page_id"), "page_id")
        elif args.get("page_id"):
            raise BridgeError("invalid_input", "page_id is not valid for create_task")
        _canonical(intent)
        return operation_id, intent

    @staticmethod
    def _last_edited_time(page: Mapping[str, Any]) -> str:
        return _required_text(page.get("last_edited_time"), "Notion last_edited_time")

    def _bind_notion_preview(self, intent: dict[str, Any]) -> dict[str, Any]:
        action = intent["action"]
        properties = intent.get("properties") or {}
        binding = self._schema_binding(
            intent["data_source_id"],
            properties,
            include_idempotency=action == "create_task",
        )
        request = {
            **intent,
            "intent_digest": _digest(intent),
            "property_schema": binding,
            "schema_digest": _digest(binding),
            "normalized_changes": (
                {"in_trash": True}
                if action == "archive_task"
                else self._property_projection(properties)
            ),
        }
        if action != "create_task":
            page = self._notion("GET", f"/pages/{urllib.parse.quote(intent['page_id'])}")
            self._verify_page_identity(page, intent["page_id"], intent["data_source_id"])
            request["expected_last_edited_time"] = self._last_edited_time(page)
        return request

    def _assert_schema_unchanged(self, request: Mapping[str, Any]) -> None:
        binding = self._schema_binding(
            request["data_source_id"],
            request.get("properties") or {},
            include_idempotency=request["action"] == "create_task",
            validate_values=False,
        )
        if _digest(binding) != request.get("schema_digest") or binding != request.get("property_schema"):
            raise BridgeError("schema_drift", "Notion property schema changed after approval")

    def _mutation_matches(self, page: dict[str, Any], request: Mapping[str, Any]) -> bool:
        if request["action"] == "archive_task":
            return page.get("in_trash") is True
        return self._properties_match(page, request.get("properties") or {})

    def _verify_mutation(self, page: dict[str, Any], request: Mapping[str, Any]) -> None:
        if not self._mutation_matches(page, request):
            message = (
                "Notion read-back did not confirm the page was archived"
                if request["action"] == "archive_task"
                else "Notion read-back did not match the requested properties"
            )
            raise BridgeError("verification_failed", message)

    def _read_back_evidence(
        self, page: dict[str, Any], request: Mapping[str, Any]
    ) -> dict[str, Any]:
        evidence: dict[str, Any] = {
            "page_id": page.get("id"),
            "data_source_id": self._page_data_source(page),
            "last_edited_time": self._last_edited_time(page),
        }
        if request["action"] == "archive_task":
            evidence["in_trash"] = page.get("in_trash")
        else:
            actual = page.get("properties") or {}
            evidence["properties"] = self._property_projection(
                {name: actual.get(name) for name in request.get("properties") or {}}
            )
        return evidence

    def mutate_notion(self, args: dict[str, Any]) -> dict[str, Any]:
        mode = args.get("mode", "preview")
        if mode not in {"preview", "apply"}:
            raise BridgeError("invalid_input", "mode must be preview or apply")
        operation_id, intent = self._notion_request(args)
        kind = "notion_mutation"
        now = self.clock()
        if mode == "preview":
            request = self._bind_notion_preview(intent)
            expires_at = now + self.config.preview_ttl_seconds
            preview_digest = _digest(
                {"kind": kind, "request": request, "expires_at": expires_at}
            )
            self.store.save_preview(operation_id, kind, request, preview_digest, expires_at, now)
            return {
                "mode": "preview",
                "operation_id": operation_id,
                "preview_digest": preview_digest,
                "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(),
                "preview": request,
            }
        preview_digest = _required_text(args.get("preview_digest"), "preview_digest")
        preview_row, request = self.store.resolve_preview(
            operation_id, kind, intent, preview_digest
        )
        duplicate = self.store.receipt(operation_id, kind, request)
        if duplicate is not None:
            return duplicate
        recovering = self.store.has_stale_claim(operation_id, kind, request, now)
        preview_expired = float(preview_row["expires_at"]) <= now
        if preview_expired and not recovering:
            raise BridgeError("preview_expired", "The approved preview has expired")
        claimed = self.store.claim(operation_id, kind, request, now)
        if claimed is not None:
            return claimed
        # A stale claim means a previous network call may already have committed.
        # Recovery is therefore read-only and must retain the claim until exact
        # remote evidence settles it.
        mutation_dispatched = recovering
        try:
            schema_error: BridgeError | None = None
            try:
                self._assert_schema_unchanged(request)
            except BridgeError as exc:
                if not recovering or exc.code != "schema_drift":
                    raise
                schema_error = exc
            already_satisfied = False
            if request["action"] == "create_task":
                properties = dict(request["properties"])
                properties[self.config.notion_idempotency_property] = {
                    "rich_text": [{"type": "text", "text": {"content": operation_id}}]
                }
                matches = self._create_matches(request["data_source_id"], operation_id)
                if matches:
                    already_satisfied = True
                    page = matches[0]
                else:
                    if recovering:
                        if schema_error is not None:
                            raise schema_error
                        raise BridgeError(
                            "ambiguous_commit",
                            "The earlier Notion create is still unverified; no retry was sent",
                        )
                    try:
                        mutation_dispatched = True
                        page = self._notion(
                            "POST",
                            "/pages",
                            {
                                "parent": {
                                    "type": "data_source_id",
                                    "data_source_id": request["data_source_id"],
                                },
                                "properties": properties,
                            },
                        )
                    except BridgeError:
                        recovered = self._create_matches(request["data_source_id"], operation_id)
                        if not recovered:
                            raise
                        page = recovered[0]
                page_id = _required_text(page.get("id"), "Notion page id")
                readback = self._notion("GET", f"/pages/{urllib.parse.quote(page_id)}")
                self._verify_page_identity(readback, page_id, request["data_source_id"])
                self._verify_properties(readback, properties)
            else:
                page_id = request["page_id"]
                readback = self._notion("GET", f"/pages/{urllib.parse.quote(page_id)}")
                self._verify_page_identity(readback, page_id, request["data_source_id"])
                if recovering and self._mutation_matches(readback, request):
                    already_satisfied = True
                elif recovering:
                    if schema_error is not None:
                        raise schema_error
                    raise BridgeError(
                        "ambiguous_commit",
                        "The earlier Notion update is still unverified; no retry was sent",
                    )
                else:
                    if self._last_edited_time(readback) != request["expected_last_edited_time"]:
                        raise BridgeError("version_conflict", "Notion page changed after approval")
                    if self._mutation_matches(readback, request):
                        already_satisfied = True
                    else:
                        mutation_dispatched = True
                        payload = (
                            {"in_trash": True}
                            if request["action"] == "archive_task"
                            else {"properties": request["properties"]}
                        )
                        self._notion(
                            "PATCH",
                            f"/pages/{urllib.parse.quote(page_id)}",
                            payload,
                        )
                        readback = self._notion("GET", f"/pages/{urllib.parse.quote(page_id)}")
                        self._verify_page_identity(readback, page_id, request["data_source_id"])
                self._verify_mutation(readback, request)
            evidence = self._read_back_evidence(readback, request)
            verified_at = self.clock()
            receipt = {
                "mode": "applied",
                "operation_id": operation_id,
                "action": request["action"],
                "page_id": page_id,
                "data_source_id": request["data_source_id"],
                "verified": True,
                "duplicate": False,
                "already_satisfied": already_satisfied,
                "schema_digest": request["schema_digest"],
                "schema_verified": schema_error is None,
                "request_hash": _digest(request),
                "read_back_hash": _digest(evidence),
                "expected_last_edited_time": request.get("expected_last_edited_time"),
                "observed_last_edited_time": evidence["last_edited_time"],
                "verified_at": datetime.fromtimestamp(verified_at, timezone.utc).isoformat(),
            }
            return self.store.verify(operation_id, kind, request, page_id, receipt, verified_at)
        except Exception:
            if not mutation_dispatched:
                self.store.abandon(operation_id, kind)
            raise

    @staticmethod
    def _page_data_source(page: Mapping[str, Any]) -> str | None:
        parent = page.get("parent")
        if not isinstance(parent, dict):
            return None
        value = parent.get("data_source_id") or parent.get("database_id")
        return value if isinstance(value, str) else None

    def _validate_activation_preview(
        self,
        response: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        normalized = response.get("normalizedPayload")
        notion = request["notion"]
        if (
            response.get("ok") is not True
            or response.get("result") != "preview"
            or response.get("contractVersion") != "notion-activation-v1"
            or response.get("operationId") != request["operationId"]
            or not isinstance(normalized, dict)
            or normalized.get("operationId") != request["operationId"]
            or normalized.get("notionPageId") != notion["pageId"]
            or normalized.get("notionDataSourceId") != notion["dataSourceId"]
            or normalized.get("notionUrl") != notion["url"]
            or not self._same_timestamp(
                normalized.get("notionLastEditedAt"), notion["lastEditedAt"]
            )
            or not self._activation_task_matches(normalized.get("task"), request["task"])
            or normalized.get("workBlock") != request["workBlock"]
        ):
            raise BridgeError("receipt_mismatch", "FlowState preview identity did not match the request")
        return response

    @staticmethod
    def _same_timestamp(left: Any, right: Any) -> bool:
        try:
            return _parse_time(left) == _parse_time(right)
        except BridgeError:
            return False

    def _activation_task_matches(self, actual: Any, requested: Mapping[str, Any]) -> bool:
        if not isinstance(actual, dict):
            return False
        requested_due_date = requested.get("dueDate")
        return (
            actual.get("title") == requested["title"]
            and actual.get("description") == requested.get("description", "")
            and actual.get("priority") == requested.get("priority")
            and actual.get("projectId") == requested.get("projectId")
            and (
                actual.get("dueDate") is None
                if requested_due_date is None
                else self._same_timestamp(actual.get("dueDate"), requested_due_date)
            )
        )

    def _activation_provenance_matches(
        self, provenance: Any, notion: Mapping[str, Any]
    ) -> bool:
        return (
            isinstance(provenance, dict)
            and provenance.get("source") == "notion"
            and provenance.get("externalId") == notion["pageId"]
            and provenance.get("dataSourceId") == notion["dataSourceId"]
            and provenance.get("url") == notion["url"]
            and self._same_timestamp(
                provenance.get("lastEditedAt"), notion["lastEditedAt"]
            )
        )

    def _validate_activation_receipt(
        self,
        response: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        receipt = response.get("receipt")
        read_back = receipt.get("readBack") if isinstance(receipt, dict) else None
        notion = request["notion"]
        canonical_revision = receipt.get("canonicalRevision") if isinstance(receipt, dict) else None
        change_sequence = receipt.get("changeSequence") if isinstance(receipt, dict) else None
        read_back_hash = receipt.get("readBackHash") if isinstance(receipt, dict) else None
        if (
            response.get("ok") is not True
            or response.get("result") != "committed"
            or not isinstance(receipt, dict)
            or receipt.get("contractVersion") != "notion-activation-v1"
            or receipt.get("source") != "notion"
            or receipt.get("externalId") != notion["pageId"]
            or receipt.get("operationId") != request["operationId"]
            or receipt.get("entityType") != "task"
            or receipt.get("action") != "activate"
            or not isinstance(receipt.get("replayed"), bool)
            or not self._activation_provenance_matches(receipt.get("provenance"), notion)
            or not isinstance(canonical_revision, int)
            or isinstance(canonical_revision, bool)
            or canonical_revision < 1
            or not isinstance(change_sequence, int)
            or isinstance(change_sequence, bool)
            or change_sequence < 1
            or not isinstance(read_back_hash, str)
            or len(read_back_hash) != 64
            or any(character not in "0123456789abcdef" for character in read_back_hash)
            or not isinstance(receipt.get("canonicalUpdatedAt"), str)
            or not isinstance(receipt.get("committedAt"), str)
            or not isinstance(read_back, dict)
            or not self._activation_task_matches(read_back, request["task"])
            or read_back.get("id") != receipt.get("entityId")
            or read_back.get("canonicalRevision") != canonical_revision
            or read_back.get("canonicalUpdatedAt") != receipt.get("canonicalUpdatedAt")
            or read_back.get("externalSource") != "notion"
            or read_back.get("externalId") != notion["pageId"]
            or read_back.get("externalDataSourceId") != notion["dataSourceId"]
            or read_back.get("externalUrl") != notion["url"]
            or not self._same_timestamp(
                read_back.get("externalLastEditedAt"), notion["lastEditedAt"]
            )
            or not self._activation_provenance_matches(read_back.get("provenance"), notion)
            or read_back_hash != _digest(read_back).removeprefix("sha256:")
        ):
            raise BridgeError("receipt_mismatch", "FlowState receipt identity did not match the request")
        _parse_time(receipt["canonicalUpdatedAt"])
        _parse_time(receipt["committedAt"])
        instances = read_back.get("instances")
        work_block = request["workBlock"]
        if work_block is not None and (not isinstance(instances, list) or not any(
            isinstance(instance, dict)
            and instance.get("scheduledDate") == work_block.get("scheduledDate")
            and instance.get("scheduledTime") == work_block.get("scheduledTime")
            and instance.get("duration") == work_block.get("duration")
            for instance in instances
        )):
            raise BridgeError(
                "verification_failed", "FlowState read-back omitted the approved work block"
            )
        return receipt

    def _activation_request(self, args: dict[str, Any], page: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        operation_id = _required_text(args.get("operation_id"), "operation_id", 160)
        page_id = _required_text(args.get("page_id"), "page_id")
        data_source_id = self._data_source_id(args)
        parent_source = self._page_data_source(page)
        if parent_source != data_source_id:
            raise BridgeError("provenance_mismatch", "Notion page does not belong to the requested data source")
        task = dict(_object(args.get("task"), "task"))
        task["title"] = _required_text(task.get("title"), "task.title", 500)
        allowed_task = {"title", "description", "priority", "dueDate", "projectId"}
        if set(task) - allowed_task:
            raise BridgeError("invalid_input", "task contains unsupported fields")
        work_block_value = args.get("work_block")
        work_block = None
        if work_block_value is not None:
            work_block = _object(work_block_value, "work_block")
            if not work_block:
                raise BridgeError("invalid_input", "work_block must not be empty")
        request = {
            "operationId": operation_id,
            "notion": {
                "pageId": page_id,
                "dataSourceId": data_source_id,
                "url": page.get("url"),
                "lastEditedAt": page.get("last_edited_time"),
            },
            "task": task,
            "workBlock": work_block,
        }
        _canonical(request)
        return operation_id, request

    def activate(self, args: dict[str, Any]) -> dict[str, Any]:
        mode = args.get("mode", "preview")
        if mode not in {"preview", "apply"}:
            raise BridgeError("invalid_input", "mode must be preview or apply")
        page_id = _required_text(args.get("page_id"), "page_id")
        page = self._notion("GET", f"/pages/{urllib.parse.quote(page_id)}")
        if page.get("id") != page_id:
            raise BridgeError("provenance_mismatch", "Notion returned a different page")
        operation_id, request = self._activation_request(args, page)
        data_source_id = request["notion"]["dataSourceId"]
        kind = "flowstate_activation"
        now = self.clock()
        endpoint = self.config.flowstate_base_url.rstrip("/") + "/api/integrations/notion/activations"
        if mode == "preview":
            response = self.transport.request(
                "POST",
                endpoint,
                headers=self._flowstate_headers(),
                payload={**request, "preview": True},
            )
            identity = self._validate_activation_preview(response, request)
            preview_digest = identity.get("previewDigest")
            preview_digest = _required_text(preview_digest, "FlowState preview digest")
            expires_raw = identity.get("previewExpiresAt")
            expires_at = _parse_time(expires_raw)
            if expires_at <= now or expires_at > now + 3600:
                raise BridgeError("invalid_response", "FlowState preview expiry is outside the allowed window")
            self.store.save_preview(
                operation_id, kind, request, preview_digest, expires_at, now
            )
            return {
                "mode": "preview",
                "operation_id": operation_id,
                "page_id": page_id,
                "data_source_id": data_source_id,
                "preview_digest": preview_digest,
                "expires_at": expires_raw,
                "already_activated": response.get("alreadyActivated") is True,
                "preview": response.get("normalizedPayload", {}),
            }
        preview_digest = _required_text(args.get("preview_digest"), "preview_digest")
        duplicate = self.store.receipt(operation_id, kind, request)
        if duplicate is not None:
            return duplicate
        recovering = self.store.has_stale_claim(operation_id, kind, request, now)
        preview_row = self.store.require_preview(
            operation_id,
            kind,
            request,
            preview_digest,
            now,
            allow_expired=recovering,
        )
        claimed = self.store.claim(operation_id, kind, request, now)
        if claimed is not None:
            return claimed
        mutation_dispatched = False
        try:
            mutation_dispatched = True
            response = self.transport.request(
                "POST",
                endpoint,
                headers=self._flowstate_headers(),
                payload={
                    **request,
                    "preview": False,
                    "previewDigest": preview_digest,
                    "previewExpiresAt": datetime.fromtimestamp(
                        float(preview_row["expires_at"]), timezone.utc
                    ).isoformat(),
                },
            )
            identity = self._validate_activation_receipt(response, request)
            flowstate_id = identity.get("entityId")
            flowstate_id = _required_text(flowstate_id, "FlowState task id")
            receipt = {
                "mode": "applied",
                "operation_id": operation_id,
                "page_id": page_id,
                "data_source_id": data_source_id,
                "flowstate_task_id": flowstate_id,
                "verified": True,
                "canonical_receipt": dict(identity),
                "duplicate": False,
            }
            return self.store.verify(operation_id, kind, request, flowstate_id, receipt, self.clock())
        except Exception:
            if not mutation_dispatched:
                self.store.abandon(operation_id, kind)
            raise


__all__ = ["Bridge", "BridgeConfig", "BridgeError", "JsonTransport", "ReceiptStore"]
