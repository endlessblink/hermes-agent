import hashlib

import pytest

from tools.flowstate_receipts import (
    CanonicalReceiptError,
    canonical_json_hash,
    validate_canonical_receipt,
)


REQUEST_HASH = "a" * 64
OPERATION_ID = "operation-1"
COMMITTED_AT = "2026-07-15T12:00:00.000Z"


def _read_back():
    return {
        "id": "task-1",
        "title": "Clarified task",
        "canonicalRevision": 8,
    }


def _response(*, status="committed", receipt_overrides=None, outer_overrides=None):
    read_back = _read_back()
    receipt = {
        "ok": True,
        "status": status,
        "operationId": OPERATION_ID,
        "requestHash": REQUEST_HASH,
        "contractVersion": "task-v1",
        "source": "local-api",
        "entityType": "task",
        "action": "patch",
        "entityId": "task-1",
        "canonicalRevision": 8,
        "changeSequence": 42,
        "committedAt": COMMITTED_AT,
        "readBack": read_back,
        "readBackHash": canonical_json_hash(read_back),
    }
    receipt.update(receipt_overrides or {})
    response = {
        "ok": True,
        "result": "committed",
        "requestHash": REQUEST_HASH,
        "receipt": receipt,
    }
    response.update(outer_overrides or {})
    return response


def _validate(response, **overrides):
    expected = {
        "expected_operation_id": OPERATION_ID,
        "expected_request_hash": REQUEST_HASH,
        "expected_action": "patch",
        "expected_entity_id": "task-1",
    }
    expected.update(overrides)
    return validate_canonical_receipt(response, **expected)


def test_canonical_json_hash_uses_sorted_compact_utf8_json():
    canonical = '{"aa":1,"b":[true,null],"hebrew":"שלום"}'

    assert canonical_json_hash({"hebrew": "שלום", "b": [True, None], "aa": 1}) == (
        hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    )


def test_validator_rejects_legacy_postgres_jsonb_read_back_hash():
    postgres_text = '{"b": [true, null], "aa": 1}'
    response = _response(
        receipt_overrides={
            "readBack": {"aa": 1, "b": [True, None]},
            "readBackHash": hashlib.sha256(postgres_text.encode("utf-8")).hexdigest(),
        }
    )

    with pytest.raises(CanonicalReceiptError):
        _validate(response)


@pytest.mark.parametrize("status", ["committed", "replayed"])
def test_validator_accepts_committed_and_byte_identical_replayed_receipts(status):
    response = _response(status=status)

    assert _validate(response) == response["receipt"]


@pytest.mark.parametrize(
    ("status", "replayed"),
    [("committed", False), ("replayed", True)],
)
def test_validator_accepts_optional_consistent_legacy_replayed_alias(status, replayed):
    response = _response(status=status, receipt_overrides={"replayed": replayed})

    assert _validate(response) == response["receipt"]


@pytest.mark.parametrize(
    ("status", "replayed"),
    [("committed", True), ("replayed", False), ("replayed", "true")],
)
def test_validator_rejects_contradictory_legacy_replayed_alias(status, replayed):
    response = _response(status=status, receipt_overrides={"replayed": replayed})

    with pytest.raises(CanonicalReceiptError):
        _validate(response)


@pytest.mark.parametrize(
    ("receipt_overrides", "outer_overrides"),
    [
        ({"operationId": "other"}, {}),
        ({"requestHash": "b" * 64}, {}),
        ({}, {"requestHash": "b" * 64}),
        ({"canonicalRevision": 0}, {}),
        ({"canonicalRevision": True}, {}),
        ({"changeSequence": 0}, {}),
        ({"changeSequence": 1.5}, {}),
        ({"status": "queued"}, {}),
        ({"readBackHash": "c" * 64}, {}),
        ({"readBack": {"id": "task-1", "canonicalRevision": 7}}, {}),
        ({"action": "delete"}, {}),
        ({"entityId": "other-task"}, {}),
        ({}, {"ok": False}),
        ({}, {"result": "queued"}),
    ],
)
def test_validator_rejects_malformed_mismatched_and_http_only_successes(
    receipt_overrides,
    outer_overrides,
):
    response = _response(
        receipt_overrides=receipt_overrides,
        outer_overrides=outer_overrides,
    )

    with pytest.raises(CanonicalReceiptError):
        _validate(response)


def test_validator_rejects_missing_receipt_even_when_http_payload_says_ok():
    with pytest.raises(CanonicalReceiptError):
        _validate({"ok": True, "result": "committed", "requestHash": REQUEST_HASH})


def test_validator_rejects_replay_with_altered_read_back():
    response = _response(status="replayed")
    response["receipt"]["readBack"]["title"] = "Altered after hashing"

    with pytest.raises(CanonicalReceiptError):
        _validate(response)


def test_validator_requires_exact_affected_task_bindings_for_multi_row_mutations():
    survivor_read_back = {"id": "survivor-1", "canonicalRevision": 8}
    duplicate_read_back = {"id": "duplicate-1", "canonicalRevision": 5}
    response = _response(
        receipt_overrides={
            "action": "merge",
            "entityId": "survivor-1",
            "readBack": survivor_read_back,
            "readBackHash": canonical_json_hash(survivor_read_back),
            "affected": [
                {
                    "entityType": "task",
                    "entityId": "survivor-1",
                    "action": "update",
                    "canonicalRevision": 8,
                    "changeSequence": 42,
                    "readBack": survivor_read_back,
                    "readBackHash": canonical_json_hash(survivor_read_back),
                },
                {
                    "entityType": "task",
                    "entityId": "duplicate-1",
                    "action": "archive",
                    "canonicalRevision": 5,
                    "changeSequence": 43,
                    "readBack": duplicate_read_back,
                    "readBackHash": canonical_json_hash(duplicate_read_back),
                },
            ],
        }
    )

    receipt = _validate(
        response,
        expected_action="merge",
        expected_entity_id="survivor-1",
        expected_affected_actions={
            "survivor-1": "update",
            "duplicate-1": "archive",
        },
    )
    assert len(receipt["affected"]) == 2

    response["receipt"]["affected"][1]["entityId"] = "unapproved-task"
    with pytest.raises(CanonicalReceiptError):
        _validate(
            response,
            expected_action="merge",
            expected_entity_id="survivor-1",
            expected_affected_actions={
                "survivor-1": "update",
                "duplicate-1": "archive",
            },
        )


def test_validator_rejects_primary_affected_entry_after_secondary_entry():
    primary_read_back = {"id": "survivor-1", "canonicalRevision": 8}
    secondary_read_back = {"id": "duplicate-1", "canonicalRevision": 5}
    response = _response(
        receipt_overrides={
            "action": "merge",
            "entityId": "survivor-1",
            "readBack": primary_read_back,
            "readBackHash": canonical_json_hash(primary_read_back),
            "affected": [
                {
                    "entityType": "task",
                    "entityId": "duplicate-1",
                    "action": "archive",
                    "canonicalRevision": 5,
                    "changeSequence": 43,
                    "readBack": secondary_read_back,
                    "readBackHash": canonical_json_hash(secondary_read_back),
                },
                {
                    "entityType": "task",
                    "entityId": "survivor-1",
                    "action": "update",
                    "canonicalRevision": 8,
                    "changeSequence": 42,
                    "readBack": primary_read_back,
                    "readBackHash": canonical_json_hash(primary_read_back),
                },
            ],
        }
    )

    with pytest.raises(CanonicalReceiptError):
        _validate(
            response,
            expected_action="merge",
            expected_entity_id="survivor-1",
            expected_affected_actions={
                "survivor-1": "update",
                "duplicate-1": "archive",
            },
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("canonicalRevision", 7),
        ("changeSequence", 41),
        ("readBack", {"id": "survivor-1", "canonicalRevision": 8, "changed": True}),
        ("readBackHash", "b" * 64),
    ],
)
def test_validator_rejects_primary_affected_proof_mismatch(field, value):
    primary_read_back = {"id": "survivor-1", "canonicalRevision": 8}
    primary = {
        "entityType": "task",
        "entityId": "survivor-1",
        "action": "update",
        "canonicalRevision": 8,
        "changeSequence": 42,
        "readBack": primary_read_back,
        "readBackHash": canonical_json_hash(primary_read_back),
    }
    primary[field] = value
    if field == "readBack":
        primary["readBackHash"] = canonical_json_hash(value)
    response = _response(
        receipt_overrides={
            "action": "merge",
            "entityId": "survivor-1",
            "readBack": primary_read_back,
            "readBackHash": canonical_json_hash(primary_read_back),
            "affected": [primary],
        }
    )

    with pytest.raises(CanonicalReceiptError):
        _validate(
            response,
            expected_action="merge",
            expected_entity_id="survivor-1",
            expected_affected_actions={"survivor-1": "update"},
        )


def test_validator_rejects_affected_read_back_hash_or_identity_mismatch():
    response = _response(
        receipt_overrides={
            "affected": [
                {
                    "entityType": "task",
                    "entityId": "task-1",
                    "action": "update",
                    "canonicalRevision": 8,
                    "changeSequence": 42,
                    "readBack": {"id": "other-task", "canonicalRevision": 8},
                    "readBackHash": "b" * 64,
                }
            ]
        }
    )

    with pytest.raises(CanonicalReceiptError):
        _validate(
            response,
            expected_affected_actions={"task-1": "update"},
        )


def test_validator_requires_affected_read_back_proof_for_exact_actions():
    response = _response(
        receipt_overrides={
            "affected": [
                {
                    "entityType": "task",
                    "entityId": "task-1",
                    "action": "update",
                    "canonicalRevision": 8,
                    "changeSequence": 42,
                }
            ]
        }
    )

    with pytest.raises(CanonicalReceiptError):
        _validate(
            response,
            expected_affected_actions={"task-1": "update"},
        )
