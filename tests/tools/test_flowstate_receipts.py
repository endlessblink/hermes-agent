import hashlib
import json

import pytest

from tools.flowstate_receipts import (
    CanonicalReceiptError,
    canonical_json_sha256,
    postgres_jsonb_sha256,
    validate_canonical_receipt,
    validate_nested_canonical_receipt,
)


def _receipt(**overrides):
    read_back = {
        "canonicalRevision": 8,
        "id": "task-1",
        "title": "Clarified task",
    }
    receipt = {
        "ok": True,
        "status": "committed",
        "operationId": "operation-1",
        "requestHash": "a" * 64,
        "canonicalRevision": 8,
        "changeSequence": 42,
        "committedAt": "2026-07-15T10:30:00Z",
        "readBack": read_back,
        "readBackHash": canonical_json_sha256(read_back),
    }
    receipt.update(overrides)
    return receipt


def test_canonical_json_hash_is_stable_across_key_order_and_unicode():
    left = {"title": "שלום", "nested": {"b": 2, "a": 1}}
    right = {"nested": {"a": 1, "b": 2}, "title": "שלום"}

    expected = hashlib.sha256(
        json.dumps(
            left,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    assert canonical_json_sha256(left) == expected
    assert canonical_json_sha256(right) == expected


@pytest.mark.parametrize("value", [1.0, -0.0, 1e-7, 1.5])
def test_canonical_json_hash_rejects_non_integer_numbers(value):
    with pytest.raises(TypeError):
        canonical_json_sha256({"value": value})


@pytest.mark.parametrize("value", [{"מפתח": "value"}, {"value": "\ud800"}])
def test_canonical_json_hash_rejects_values_outside_cross_language_subset(value):
    with pytest.raises(TypeError):
        canonical_json_sha256(value)


def test_postgres_jsonb_hash_matches_existing_flowstate_receipts():
    value = {"canonicalRevision": 3, "title": "Task", "id": "task-1"}
    serialized = '{"id": "task-1", "title": "Task", "canonicalRevision": 3}'

    assert postgres_jsonb_sha256(value) == hashlib.sha256(serialized.encode()).hexdigest()


def test_nested_validator_accepts_current_postgres_hash_and_binds_identity():
    read_back = {
        "id": "task-1",
        "canonicalRevision": 8,
        "canonicalUpdatedAt": "2026-07-15T10:30:00Z",
        "title": "Clarified task",
    }
    receipt = {
        "contractVersion": "task-v1",
        "operationId": "operation-1",
        "source": "local-api",
        "entityType": "task",
        "action": "patch",
        "entityId": "task-1",
        "canonicalRevision": 8,
        "canonicalUpdatedAt": "2026-07-15T10:30:00Z",
        "changeSequence": 42,
        "committedAt": "2026-07-15T10:30:01Z",
        "replayed": False,
        "readBack": read_back,
        "readBackHash": postgres_jsonb_sha256(read_back),
    }

    assert validate_nested_canonical_receipt(
        receipt,
        expected={
            "contractVersion": "task-v1",
            "operationId": "operation-1",
            "source": "local-api",
            "entityType": "task",
            "action": "patch",
            "entityId": "task-1",
        },
    ) == receipt

    with pytest.raises(CanonicalReceiptError) as exc_info:
        validate_nested_canonical_receipt(
            receipt,
            expected={"operationId": "another-operation"},
        )
    assert exc_info.value.code == "receipt_identity_mismatch"


@pytest.mark.parametrize("status", ["committed", "replayed"])
def test_validates_committed_and_replayed_receipts(status):
    receipt = _receipt(status=status)

    assert validate_canonical_receipt(
        receipt,
        expected_operation_id="operation-1",
        expected_request_hash="a" * 64,
    ) == receipt


@pytest.mark.parametrize(
    "overrides,expected_code",
    [
        ({"ok": False}, "not_committed"),
        ({"status": "queued"}, "not_committed"),
        ({"operationId": "other-operation"}, "operation_mismatch"),
        ({"requestHash": "b" * 64}, "request_mismatch"),
        ({"canonicalRevision": 0}, "invalid_revision"),
        ({"canonicalRevision": True}, "invalid_revision"),
        ({"changeSequence": 0}, "invalid_sequence"),
        ({"changeSequence": 1.5}, "invalid_sequence"),
        ({"committedAt": "not-a-timestamp"}, "invalid_committed_at"),
        ({"readBack": None}, "invalid_read_back"),
        ({"readBackHash": "f" * 64}, "read_back_hash_mismatch"),
    ],
)
def test_rejects_malformed_mismatched_or_forged_receipts(overrides, expected_code):
    with pytest.raises(CanonicalReceiptError) as exc_info:
        validate_canonical_receipt(
            _receipt(**overrides),
            expected_operation_id="operation-1",
            expected_request_hash="a" * 64,
        )

    assert exc_info.value.code == expected_code


def test_rejects_http_only_success_without_a_canonical_receipt():
    with pytest.raises(CanonicalReceiptError) as exc_info:
        validate_canonical_receipt({"ok": True})

    assert exc_info.value.code == "not_committed"


def test_rejects_altered_replay_bound_to_another_request():
    with pytest.raises(CanonicalReceiptError) as exc_info:
        validate_canonical_receipt(
            _receipt(status="replayed", requestHash="b" * 64),
            expected_operation_id="operation-1",
            expected_request_hash="a" * 64,
        )

    assert exc_info.value.code == "request_mismatch"
