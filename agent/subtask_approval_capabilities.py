"""Ephemeral proof that one session approved one exact subtask mutation."""

from __future__ import annotations

import copy
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$"
)
_MAX_SAFE_INTEGER = 2**53 - 1
_PROOF_KEYS = (
    "taskId",
    "operationId",
    "baseRevision",
    "operations",
    "previewDigest",
    "previewExpiresAt",
    "requestHash",
    "proposalId",
    "proposalRevision",
)


class ApprovalCapabilityError(ValueError):
    """The approval capability is absent, stale, or does not match exactly."""


@dataclass
class _Capability:
    session_key: str
    proof: dict[str, Any]
    expires_at: datetime


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _parse_expiry(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 64 or not _TIMESTAMP_RE.fullmatch(value):
        raise ApprovalCapabilityError("approval proof expiry is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApprovalCapabilityError("approval proof expiry is invalid") from exc
    if parsed.tzinfo is None:
        raise ApprovalCapabilityError("approval proof expiry is invalid")
    return parsed.astimezone(timezone.utc)


def _validate_proof(proof: Any) -> tuple[dict[str, Any], datetime]:
    if not isinstance(proof, dict) or set(proof) != set(_PROOF_KEYS):
        raise ApprovalCapabilityError("approval proof is invalid")
    for key, limit in {"taskId": 160, "operationId": 160, "proposalId": 120}.items():
        value = proof[key]
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > limit
        ):
            raise ApprovalCapabilityError("approval proof identity is invalid")
    if any(
        not _positive_int(proof[key]) or proof[key] > _MAX_SAFE_INTEGER
        for key in ("baseRevision", "proposalRevision")
    ):
        raise ApprovalCapabilityError("approval proof revision is invalid")
    if (
        not isinstance(proof["operations"], list)
        or not 1 <= len(proof["operations"]) <= 50
        or not all(isinstance(item, dict) for item in proof["operations"])
    ):
        raise ApprovalCapabilityError("approval proof operations are invalid")
    if any(
        not isinstance(proof[key], str) or not _SHA256_RE.fullmatch(proof[key])
        for key in ("previewDigest", "requestHash")
    ):
        raise ApprovalCapabilityError("approval proof digest is invalid")
    expiry = _parse_expiry(proof["previewExpiresAt"])
    return copy.deepcopy(proof), expiry


def validate_approval_proof(proof: Any) -> dict[str, Any]:
    """Validate and detach a canonical proof without registering a token."""
    normalized, _ = _validate_proof(proof)
    return normalized


class SubtaskApprovalCapabilityRegistry:
    """Thread-safe in-memory registry for session-scoped exact approvals."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None):
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._capabilities: dict[str, _Capability] = {}
        self._invalidated_revisions: dict[tuple[str, str, str], int] = {}

    def register(self, session_key: str, proof: dict[str, Any]) -> str:
        if not isinstance(session_key, str) or not session_key:
            raise ApprovalCapabilityError("approval session is invalid")
        normalized, expires_at = _validate_proof(proof)
        if expires_at <= self._clock():
            raise ApprovalCapabilityError("approval proof has expired")
        lineage = (session_key, normalized["taskId"], normalized["proposalId"])
        with self._lock:
            if normalized["proposalRevision"] <= self._invalidated_revisions.get(lineage, 0):
                raise ApprovalCapabilityError("approval proposal revision was invalidated")
            token = secrets.token_urlsafe(32)
            while token in self._capabilities:
                token = secrets.token_urlsafe(32)
            self._capabilities[token] = _Capability(session_key, normalized, expires_at)
            return token

    def authorize(self, session_key: str, capability: str, request: dict[str, Any]) -> None:
        if not isinstance(capability, str) or not capability:
            raise ApprovalCapabilityError("approval capability is required")
        with self._lock:
            record = self._capabilities.get(capability)
            if record is None:
                raise ApprovalCapabilityError("approval capability is unknown")
            if record.expires_at <= self._clock():
                self._capabilities.pop(capability, None)
                raise ApprovalCapabilityError("approval capability has expired")
            if record.session_key != session_key:
                raise ApprovalCapabilityError("approval capability belongs to another session")
            if request != record.proof:
                raise ApprovalCapabilityError("approval capability proof does not match")

    def invalidate_proposal(
        self,
        session_key: str,
        task_id: str,
        proposal_id: str,
        proposal_revision: int,
    ) -> int:
        if (
            not session_key
            or not isinstance(task_id, str)
            or not task_id
            or task_id != task_id.strip()
            or len(task_id) > 160
            or not isinstance(proposal_id, str)
            or not proposal_id
            or proposal_id != proposal_id.strip()
            or len(proposal_id) > 120
            or not _positive_int(proposal_revision)
            or proposal_revision > _MAX_SAFE_INTEGER
        ):
            raise ApprovalCapabilityError("approval proposal identity is invalid")
        lineage = (session_key, task_id, proposal_id)
        with self._lock:
            self._invalidated_revisions[lineage] = max(
                proposal_revision, self._invalidated_revisions.get(lineage, 0)
            )
            tokens = [
                token
                for token, record in self._capabilities.items()
                if record.session_key == session_key
                and record.proof["taskId"] == task_id
                and record.proof["proposalId"] == proposal_id
                and record.proof["proposalRevision"] <= proposal_revision
            ]
            for token in tokens:
                self._capabilities.pop(token, None)
            return len(tokens)

    def revoke_session(self, session_key: str) -> int:
        with self._lock:
            tokens = [
                token
                for token, record in self._capabilities.items()
                if record.session_key == session_key
            ]
            for token in tokens:
                self._capabilities.pop(token, None)
            lineages = [key for key in self._invalidated_revisions if key[0] == session_key]
            for lineage in lineages:
                self._invalidated_revisions.pop(lineage, None)
            return len(tokens)


subtask_approval_capabilities = SubtaskApprovalCapabilityRegistry()
