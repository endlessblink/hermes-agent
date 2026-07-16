"""Validation helpers for canonical FlowState mutation receipts.

FlowState owns durable state.  Hermes treats a mutation as successful only
after the receipt is bound to the expected operation and request and the
canonical read-back hash has been recomputed locally.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime
from typing import Any, Callable, Mapping, Optional


_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_SAFE_INTEGER = 2**53 - 1


class CanonicalReceiptError(ValueError):
    """A safe, typed receipt rejection suitable for user-facing handling."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _canonical_json(value: Any) -> str:
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise TypeError("Canonical JSON rejects unpaired surrogate strings")
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if value is None or isinstance(value, bool):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, int) and not isinstance(value, bool):
        if abs(value) <= _MAX_SAFE_INTEGER:
            return str(value)
        raise TypeError("Canonical JSON supports only safe integers")
    if isinstance(value, list):
        return "[" + ",".join(_canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(
            isinstance(key, str)
            and all(0x20 <= ord(character) <= 0x7E for character in key)
            for key in value
        ):
            raise TypeError("Canonical JSON object keys must be printable ASCII")
        return "{" + ",".join(
            f"{json.dumps(key, ensure_ascii=False)}:{_canonical_json(value[key])}"
            for key in sorted(value)
        ) + "}"
    raise TypeError("Canonical JSON supports only safe-integer JSON values")


def canonical_json_sha256(value: Any) -> str:
    """Return SHA-256 over stable UTF-8 JSON shared with FlowState."""

    canonical = _canonical_json(value)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _postgres_jsonb_text(value: Any) -> str:
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise TypeError("PostgreSQL JSONB rejects unpaired surrogate strings")
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if value is None or isinstance(value, bool):
        return json.dumps(value, separators=(",", ":"))
    if isinstance(value, int) and not isinstance(value, bool):
        if abs(value) <= _MAX_SAFE_INTEGER:
            return str(value)
        raise TypeError("PostgreSQL JSONB receipt numbers must be safe integers")
    if isinstance(value, list):
        return "[" + ", ".join(_postgres_jsonb_text(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("PostgreSQL JSONB object keys must be strings")
        keys = sorted(value, key=lambda key: (len(key.encode("utf-8")), key.encode("utf-8")))
        return "{" + ", ".join(
            f"{json.dumps(key, ensure_ascii=False)}: {_postgres_jsonb_text(value[key])}"
            for key in keys
        ) + "}"
    raise TypeError("PostgreSQL JSONB receipt contains unsupported values")


def postgres_jsonb_sha256(value: Any) -> str:
    """Hash the JSONB text format used by existing FlowState database RPCs."""

    return hashlib.sha256(_postgres_jsonb_text(value).encode("utf-8")).hexdigest()


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _aware_iso_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or "T" not in value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256_HEX_RE.fullmatch(value))


def validate_nested_canonical_receipt(
    receipt: Mapping[str, Any],
    *,
    expected: Mapping[str, Any],
    valid_read_back: Callable[[Mapping[str, Any]], bool] | None = None,
) -> Mapping[str, Any]:
    """Validate the nested receipt shape returned by current FlowState RPCs."""

    if not isinstance(receipt, Mapping) or any(
        receipt.get(field) != value for field, value in expected.items()
    ):
        raise CanonicalReceiptError(
            "receipt_identity_mismatch", "Canonical receipt identity does not match"
        )
    revision = receipt.get("canonicalRevision")
    read_back = receipt.get("readBack")
    if (
        not _positive_int(revision)
        or not _aware_iso_timestamp(receipt.get("canonicalUpdatedAt"))
        or not _positive_int(receipt.get("changeSequence"))
        or not _aware_iso_timestamp(receipt.get("committedAt"))
        or not isinstance(receipt.get("replayed"), bool)
        or not isinstance(read_back, Mapping)
        or read_back.get("id") != receipt.get("entityId")
        or read_back.get("canonicalRevision") != revision
        or read_back.get("canonicalUpdatedAt") != receipt.get("canonicalUpdatedAt")
    ):
        raise CanonicalReceiptError(
            "invalid_nested_receipt", "Canonical receipt proof fields are incomplete"
        )
    try:
        accepted_hashes = {
            canonical_json_sha256(read_back),
            postgres_jsonb_sha256(read_back),
        }
    except (TypeError, ValueError, UnicodeError):
        raise CanonicalReceiptError(
            "invalid_read_back", "Canonical receipt read-back is not valid JSON"
        ) from None
    if not _sha256(receipt.get("readBackHash")) or receipt["readBackHash"] not in accepted_hashes:
        raise CanonicalReceiptError(
            "read_back_hash_mismatch", "Canonical receipt read-back hash does not match"
        )
    if valid_read_back is not None and not valid_read_back(read_back):
        raise CanonicalReceiptError(
            "invalid_read_back", "Canonical receipt domain read-back is incomplete"
        )
    return receipt


def validate_canonical_receipt(
    receipt: Mapping[str, Any],
    *,
    expected_operation_id: Optional[str] = None,
    expected_request_hash: Optional[str] = None,
) -> Mapping[str, Any]:
    """Validate and return a committed or replayed canonical receipt.

    The function deliberately accepts operation-specific extra fields while
    enforcing the shared proof fields.  It never logs or returns request
    payloads, credentials, or server diagnostics.
    """

    if (
        not isinstance(receipt, Mapping)
        or receipt.get("ok") is not True
        or receipt.get("status") not in {"committed", "replayed"}
    ):
        raise CanonicalReceiptError("not_committed", "FlowState did not return a committed canonical receipt")

    operation_id = receipt.get("operationId")
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise CanonicalReceiptError("invalid_operation", "Canonical receipt operation identity is missing")
    if expected_operation_id is not None and not secrets.compare_digest(operation_id, expected_operation_id):
        raise CanonicalReceiptError("operation_mismatch", "Canonical receipt belongs to another operation")

    request_hash = receipt.get("requestHash")
    if not _sha256(request_hash):
        raise CanonicalReceiptError("invalid_request_hash", "Canonical receipt request hash is invalid")
    if expected_request_hash is not None:
        if not _sha256(expected_request_hash) or not secrets.compare_digest(request_hash, expected_request_hash):
            raise CanonicalReceiptError("request_mismatch", "Canonical receipt belongs to another request")

    if not _positive_int(receipt.get("canonicalRevision")):
        raise CanonicalReceiptError("invalid_revision", "Canonical receipt revision is invalid")
    if not _positive_int(receipt.get("changeSequence")):
        raise CanonicalReceiptError("invalid_sequence", "Canonical receipt change sequence is invalid")
    if not _aware_iso_timestamp(receipt.get("committedAt")):
        raise CanonicalReceiptError("invalid_committed_at", "Canonical receipt commit time is invalid")

    read_back = receipt.get("readBack")
    if read_back is None:
        raise CanonicalReceiptError("invalid_read_back", "Canonical receipt read-back is missing")
    try:
        computed_hash = canonical_json_sha256(read_back)
    except (TypeError, ValueError):
        raise CanonicalReceiptError("invalid_read_back", "Canonical receipt read-back is not valid JSON") from None

    read_back_hash = receipt.get("readBackHash")
    if not _sha256(read_back_hash) or not secrets.compare_digest(read_back_hash, computed_hash):
        raise CanonicalReceiptError("read_back_hash_mismatch", "Canonical receipt read-back hash does not match")

    return receipt
