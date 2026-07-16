"""Ephemeral, session-scoped proof for an exact Notion bridge apply."""

from __future__ import annotations

import copy
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable


_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$"
)
_TOOLS = {"notion_mutation", "notion_flowstate_activate"}


class NotionApprovalCapabilityError(ValueError):
    """The approval capability is absent, stale, or does not match exactly."""


@dataclass
class _Capability:
    session_key: str
    proof: dict[str, Any]
    expires_at: datetime


def _parse_expiry(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 64 or not _TIMESTAMP_RE.fullmatch(value):
        raise NotionApprovalCapabilityError("approval proof expiry is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NotionApprovalCapabilityError("approval proof expiry is invalid") from exc
    if parsed.tzinfo is None:
        raise NotionApprovalCapabilityError("approval proof expiry is invalid")
    return parsed.astimezone(timezone.utc)


def validate_notion_approval_proof(proof: Any) -> dict[str, Any]:
    if not isinstance(proof, dict) or set(proof) != {"tool", "apply", "previewExpiresAt"}:
        raise NotionApprovalCapabilityError("approval proof is invalid")
    if proof.get("tool") not in _TOOLS or not isinstance(proof.get("apply"), dict):
        raise NotionApprovalCapabilityError("approval proof target is invalid")
    apply = proof["apply"]
    if not apply or apply.get("mode") != "apply" or len(apply) > 16:
        raise NotionApprovalCapabilityError("approval apply request is invalid")
    common = {
        "mode", "operation_id", "data_source_id", "preview_digest",
        "preview_expires_at",
    }
    if proof["tool"] == "notion_mutation":
        action = apply.get("action")
        expected = {
            "create_task": common | {"action", "properties"},
            "update_properties": common | {"action", "page_id", "properties"},
            "set_status": common | {"action", "page_id", "status_property", "status_name"},
            "archive_task": common | {"action", "page_id"},
        }.get(action)
    else:
        expected = common | {"page_id", "task"}
        if "work_block" in apply:
            expected.add("work_block")
    if expected is None or set(apply) != expected:
        raise NotionApprovalCapabilityError("approval apply request is invalid")
    for key in ("operation_id", "data_source_id", "preview_digest", "preview_expires_at"):
        value = apply.get(key)
        if not isinstance(value, str) or not value or value != value.strip() or len(value) > 256:
            raise NotionApprovalCapabilityError("approval apply identity is invalid")
    for key in ("page_id", "status_property", "status_name"):
        if key in apply and (
            not isinstance(apply[key], str)
            or not apply[key]
            or apply[key] != apply[key].strip()
            or len(apply[key]) > 500
        ):
            raise NotionApprovalCapabilityError("approval apply identity is invalid")
    if "properties" in apply and not isinstance(apply["properties"], dict):
        raise NotionApprovalCapabilityError("approval properties are invalid")
    if "task" in apply and not isinstance(apply["task"], dict):
        raise NotionApprovalCapabilityError("approval task is invalid")
    if "work_block" in apply and not isinstance(apply["work_block"], dict):
        raise NotionApprovalCapabilityError("approval work block is invalid")
    try:
        encoded = __import__("json").dumps(
            apply, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise NotionApprovalCapabilityError("approval apply request is invalid") from exc
    if len(encoded) > 64 * 1024:
        raise NotionApprovalCapabilityError("approval apply request is too large")
    expiry = _parse_expiry(proof["previewExpiresAt"])
    if _parse_expiry(apply["preview_expires_at"]) != expiry:
        raise NotionApprovalCapabilityError("approval proof expiry does not match apply")
    return copy.deepcopy(proof), expiry


class NotionApprovalCapabilityRegistry:
    def __init__(self, *, clock: Callable[[], datetime] | None = None):
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._capabilities: dict[str, _Capability] = {}

    def register(self, session_key: str, proof: dict[str, Any]) -> str:
        if not isinstance(session_key, str) or not session_key:
            raise NotionApprovalCapabilityError("approval session is invalid")
        normalized, expires_at = validate_notion_approval_proof(proof)
        if expires_at <= self._clock():
            raise NotionApprovalCapabilityError("approval proof has expired")
        token = secrets.token_urlsafe(32)
        with self._lock:
            while token in self._capabilities:
                token = secrets.token_urlsafe(32)
            self._capabilities[token] = _Capability(session_key, normalized, expires_at)
        return token

    def authorize(self, session_key: str, capability: str, proof: dict[str, Any]) -> None:
        if not isinstance(capability, str) or not capability:
            raise NotionApprovalCapabilityError("approval capability is required")
        normalized, _ = validate_notion_approval_proof(proof)
        with self._lock:
            record = self._capabilities.get(capability)
            if record is None:
                raise NotionApprovalCapabilityError("approval capability is unknown")
            if record.expires_at <= self._clock():
                self._capabilities.pop(capability, None)
                raise NotionApprovalCapabilityError("approval capability has expired")
            if record.session_key != session_key:
                raise NotionApprovalCapabilityError("approval capability belongs to another session")
            if record.proof != normalized:
                raise NotionApprovalCapabilityError("approval capability proof does not match")

    def authorize_registered(self, session_key: str, proof: dict[str, Any]) -> None:
        """Authorize exact UI-approved proof without exposing its random token to the model."""
        normalized, _ = validate_notion_approval_proof(proof)
        with self._lock:
            expired = [
                token for token, record in self._capabilities.items()
                if record.expires_at <= self._clock()
            ]
            for token in expired:
                self._capabilities.pop(token, None)
            if not any(
                record.session_key == session_key and record.proof == normalized
                for record in self._capabilities.values()
            ):
                raise NotionApprovalCapabilityError("matching UI approval is required")

    def revoke_session(self, session_key: str) -> int:
        with self._lock:
            tokens = [
                token
                for token, record in self._capabilities.items()
                if record.session_key == session_key
            ]
            for token in tokens:
                self._capabilities.pop(token, None)
            return len(tokens)

    def invalidate(self, session_key: str, proof: dict[str, Any]) -> int:
        normalized, _ = validate_notion_approval_proof(proof)
        with self._lock:
            tokens = [
                token
                for token, record in self._capabilities.items()
                if record.session_key == session_key and record.proof == normalized
            ]
            for token in tokens:
                self._capabilities.pop(token, None)
            return len(tokens)


notion_approval_capabilities = NotionApprovalCapabilityRegistry()
