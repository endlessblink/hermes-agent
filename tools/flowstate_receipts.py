"""Strict validation for canonical FlowState mutation receipts."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Any, Callable, Mapping


_SHA256_HEX_LENGTH = 64
_VALID_STATUSES = frozenset({"committed", "replayed"})
_CANONICAL_CONTRACT_VERSION = "task-v1"


class CanonicalReceiptError(ValueError):
    """A successful HTTP response could not prove a canonical mutation."""


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json_hash(value: Any) -> str:
    """Hash compact, sorted, UTF-8 JSON without accepting non-JSON floats."""
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return _sha256(serialized)


def _postgres_jsonb_key(key: str) -> tuple[int, bytes]:
    encoded = key.encode("utf-8")
    return len(encoded), encoded


def _postgres_jsonb_text(value: Any) -> str:
    """Serialize the JSON subset used by legacy PostgreSQL jsonb receipt hashes."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite values are not valid JSON")
        return json.dumps(value, allow_nan=False)
    if isinstance(value, list):
        return "[" + ", ".join(_postgres_jsonb_text(item) for item in value) + "]"
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        entries = []
        for key in sorted(value, key=_postgres_jsonb_key):
            encoded_key = json.dumps(key, ensure_ascii=False)
            entries.append(f"{encoded_key}: {_postgres_jsonb_text(value[key])}")
        return "{" + ", ".join(entries) + "}"
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def postgres_jsonb_hash(value: Any) -> str:
    """Hash PostgreSQL jsonb text for receipts issued before canonical JSON."""
    return _sha256(_postgres_jsonb_text(value))


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_HEX_LENGTH
        and all(char in "0123456789abcdef" for char in value)
    )


def _timestamp(value: Any) -> bool:
    if not isinstance(value, str) or "T" not in value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _validate_affected(
    value: Any,
    *,
    required: bool,
    expected_actions: Mapping[str, str] | None,
) -> None:
    if value is None and not required and expected_actions is None:
        return
    if not isinstance(value, list) or not value:
        raise CanonicalReceiptError("canonical receipt affected entries are invalid")

    actual_actions: dict[str, str] = {}
    for entry in value:
        if not isinstance(entry, Mapping):
            raise CanonicalReceiptError("canonical receipt affected entries are invalid")
        entity_type = entry.get("entityType")
        entity_id = entry.get("entityId")
        action = entry.get("action")
        if (
            entity_type != "task"
            or not isinstance(entity_id, str)
            or not entity_id
            or not isinstance(action, str)
            or not action
            or not _positive_int(entry.get("canonicalRevision"))
            or not _positive_int(entry.get("changeSequence"))
        ):
            raise CanonicalReceiptError("canonical receipt affected entries are invalid")
        if entity_id in actual_actions:
            raise CanonicalReceiptError("canonical receipt affected entries are duplicated")
        actual_actions[entity_id] = action

        affected_read_back = entry.get("readBack")
        affected_read_back_hash = entry.get("readBackHash")
        if expected_actions is not None and (
            affected_read_back is None or affected_read_back_hash is None
        ):
            raise CanonicalReceiptError(
                "canonical receipt affected read-back is required"
            )
        if affected_read_back is not None or affected_read_back_hash is not None:
            if (
                not isinstance(affected_read_back, Mapping)
                or affected_read_back.get("id") != entity_id
                or affected_read_back.get("canonicalRevision")
                != entry.get("canonicalRevision")
                or not _digest(affected_read_back_hash)
            ):
                raise CanonicalReceiptError(
                    "canonical receipt affected read-back is invalid"
                )
            try:
                valid_hashes = {
                    canonical_json_hash(affected_read_back),
                    postgres_jsonb_hash(affected_read_back),
                }
            except (TypeError, ValueError):
                raise CanonicalReceiptError(
                    "canonical receipt affected read-back is invalid"
                ) from None
            if affected_read_back_hash not in valid_hashes:
                raise CanonicalReceiptError(
                    "canonical receipt affected read-back hash does not match"
                )

    if expected_actions is not None and actual_actions != dict(expected_actions):
        raise CanonicalReceiptError("canonical receipt affected identities do not match")


def validate_canonical_receipt(
    response: Any,
    *,
    expected_operation_id: str,
    expected_request_hash: str,
    expected_action: str,
    expected_entity_id: str,
    expected_affected_actions: Mapping[str, str] | None = None,
    require_affected: bool = False,
    read_back_validator: Callable[[Mapping[str, Any], Mapping[str, Any]], bool] | None = None,
) -> Mapping[str, Any]:
    """Validate an apply response without deriving the server-owned request hash."""
    if (
        not isinstance(response, Mapping)
        or response.get("ok") is not True
        or response.get("result") != "committed"
        or response.get("requestHash") != expected_request_hash
        or not _digest(expected_request_hash)
    ):
        raise CanonicalReceiptError("canonical mutation response is invalid")

    receipt = response.get("receipt")
    if not isinstance(receipt, Mapping):
        raise CanonicalReceiptError("canonical mutation receipt is missing")
    if (
        receipt.get("ok") is not True
        or receipt.get("status") not in _VALID_STATUSES
        or receipt.get("operationId") != expected_operation_id
        or receipt.get("requestHash") != expected_request_hash
        or receipt.get("contractVersion") != _CANONICAL_CONTRACT_VERSION
        or receipt.get("source") != "local-api"
        or receipt.get("entityType") != "task"
        or receipt.get("action") != expected_action
        or receipt.get("entityId") != expected_entity_id
        or not _positive_int(receipt.get("canonicalRevision"))
        or not _positive_int(receipt.get("changeSequence"))
        or not _timestamp(receipt.get("committedAt"))
    ):
        raise CanonicalReceiptError("canonical mutation receipt fields do not match")

    canonical_updated_at = receipt.get("canonicalUpdatedAt")
    if canonical_updated_at is not None and not _timestamp(canonical_updated_at):
        raise CanonicalReceiptError("canonical mutation receipt fields do not match")

    if "replayed" in receipt:
        replayed = receipt.get("replayed")
        if not isinstance(replayed, bool) or replayed != (
            receipt.get("status") == "replayed"
        ):
            raise CanonicalReceiptError("canonical mutation replay fields do not match")

    read_back = receipt.get("readBack")
    read_back_hash = receipt.get("readBackHash")
    if not isinstance(read_back, Mapping) or not _digest(read_back_hash):
        raise CanonicalReceiptError("canonical mutation read-back is invalid")
    try:
        valid_hashes = {
            canonical_json_hash(read_back),
            postgres_jsonb_hash(read_back),
        }
    except (TypeError, ValueError):
        raise CanonicalReceiptError("canonical mutation read-back is invalid") from None
    if read_back_hash not in valid_hashes:
        raise CanonicalReceiptError("canonical mutation read-back hash does not match")
    if read_back_validator is not None and not read_back_validator(read_back, receipt):
        raise CanonicalReceiptError("canonical mutation read-back does not match")

    _validate_affected(
        receipt.get("affected"),
        required=require_affected,
        expected_actions=expected_affected_actions,
    )
    return receipt
