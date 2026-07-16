from __future__ import annotations

from datetime import datetime, timezone

import pytest

from integrations.notion_flowstate_bridge.inventory import (
    InventoryError,
    reconcile_inventory,
)


NOW = datetime(2026, 7, 16, 20, 0, tzinfo=timezone.utc)


def _source(**overrides):
    source = {
        "source_id": "notion:bina",
        "scope": "all open tasks owned by Noam",
        "completeness_evidence": "Notion returned has_more=false after the final page",
        "captured_at": "2026-07-16T19:59:30Z",
        "complete": True,
        "items": [
            {
                "id": "page-1",
                "title": "Prepare launch page",
                "classification": "uncharacterized",
                "evidence": "Project is set but next action is empty",
            },
            {
                "id": "page-2",
                "title": "Send proposal",
                "classification": "characterized",
                "evidence": "Project, owner, and next action are explicit",
            },
        ],
    }
    source.update(overrides)
    return source


def test_complete_fresh_single_source_can_return_exact_counts():
    result = reconcile_inventory(
        {
            "inventory_question": "Which tasks still need characterization?",
            "sources": [_source()],
        },
        now=NOW,
    )

    assert result["verified"] is True
    assert result["exact_total"] == 2
    assert result["exact_uncharacterized"] == 1
    assert result["blocking_reasons"] == []


def test_partial_or_unknown_source_withholds_exact_counts():
    source = _source(
        complete=False,
        items=[
            {
                "id": "note-1",
                "title": "Possible task",
                "classification": "unknown",
                "evidence": "The note has no stable project field",
            }
        ],
    )
    result = reconcile_inventory(
        {"inventory_question": "How many tasks remain?", "sources": [source]},
        now=NOW,
    )

    assert result["verified"] is False
    assert result["exact_total"] is None
    assert result["exact_uncharacterized"] is None
    assert result["observed_total"] == 1
    assert result["blocking_reasons"] == [
        "source notion:bina is partial",
        "1 item has unknown characterization",
    ]


def test_cross_source_union_requires_canonical_ids_for_every_item():
    result = reconcile_inventory(
        {
            "inventory_question": "How many distinct tasks remain?",
            "sources": [
                _source(),
                _source(
                    source_id="flowstate:open",
                    scope="all open FlowState tasks",
                    items=[
                        {
                            "id": "task-1",
                            "title": "Prepare launch page",
                            "classification": "uncharacterized",
                            "evidence": "Task has no next action",
                        }
                    ],
                ),
            ],
        },
        now=NOW,
    )

    assert result["verified"] is False
    assert result["exact_total"] is None
    assert "3 cross-source items lack canonical_id" in result["blocking_reasons"]


def test_complete_fresh_cross_source_union_deduplicates_canonical_items():
    result = reconcile_inventory(
        {
            "inventory_question": "How many distinct tasks remain?",
            "sources": [
                _source(
                    items=[
                        {
                            "id": "page-1",
                            "canonical_id": "task-1",
                            "title": "Prepare launch page",
                            "classification": "characterized",
                            "evidence": "Notion contains the project and next action",
                        }
                    ]
                ),
                _source(
                    source_id="flowstate:open",
                    scope="all active FlowState tasks",
                    items=[
                        {
                            "id": "flow-7",
                            "canonical_id": "task-1",
                            "title": "Prepare launch page",
                            "classification": "characterized",
                            "evidence": "FlowState retains the Notion provenance",
                        },
                        {
                            "id": "flow-8",
                            "canonical_id": "task-2",
                            "title": "Independent personal task",
                            "classification": "uncharacterized",
                            "evidence": "The next action is empty",
                        },
                    ],
                ),
            ],
        },
        now=NOW,
    )

    assert result["verified"] is True
    assert result["exact_total"] == 2
    assert result["exact_uncharacterized"] == 1


def test_cross_source_capture_skew_withholds_exact_count():
    result = reconcile_inventory(
        {
            "inventory_question": "How many distinct tasks remain?",
            "sources": [
                _source(
                    captured_at="2026-07-16T19:55:10Z",
                    items=[
                        {
                            "id": "page-1",
                            "canonical_id": "task-1",
                            "title": "Task",
                            "classification": "characterized",
                            "evidence": "Notion evidence",
                        }
                    ],
                ),
                _source(
                    source_id="flowstate:open",
                    scope="all active FlowState tasks",
                    captured_at="2026-07-16T19:59:30Z",
                    items=[
                        {
                            "id": "flow-1",
                            "canonical_id": "task-1",
                            "title": "Task",
                            "classification": "characterized",
                            "evidence": "FlowState evidence",
                        }
                    ],
                ),
            ],
        },
        now=NOW,
    )

    assert result["verified"] is False
    assert result["exact_total"] is None
    assert "source capture times differ by more than 120 seconds" in result["blocking_reasons"]


def test_cross_source_conflict_is_explicit_and_withholds_exact_count():
    result = reconcile_inventory(
        {
            "inventory_question": "How many distinct tasks remain?",
            "sources": [
                _source(
                    items=[
                        {
                            "id": "page-1",
                            "canonical_id": "task-1",
                            "title": "Prepare launch page",
                            "classification": "characterized",
                            "evidence": "Notion has a next action",
                        }
                    ]
                ),
                _source(
                    source_id="obsidian:ledger",
                    scope="all linked task notes",
                    items=[
                        {
                            "id": "note-9",
                            "canonical_id": "task-1",
                            "title": "Prepare launch page",
                            "classification": "uncharacterized",
                            "evidence": "Ledger says next action is missing",
                        }
                    ],
                ),
            ],
        },
        now=NOW,
    )

    assert result["verified"] is False
    assert result["exact_total"] is None
    assert result["conflicts"] == [
        {
            "canonical_id": "task-1",
            "classifications": ["characterized", "uncharacterized"],
            "sources": ["notion:bina", "obsidian:ledger"],
        }
    ]
    assert "1 canonical item has conflicting classifications" in result["blocking_reasons"]


def test_stale_or_future_capture_withholds_exact_count():
    stale = reconcile_inventory(
        {
            "inventory_question": "How many tasks remain?",
            "max_age_seconds": 300,
            "sources": [_source(captured_at="2026-07-16T19:40:00Z")],
        },
        now=NOW,
    )
    future = reconcile_inventory(
        {
            "inventory_question": "How many tasks remain?",
            "sources": [_source(captured_at="2026-07-16T20:02:00Z")],
        },
        now=NOW,
    )

    assert stale["verified"] is False
    assert "source notion:bina is stale" in stale["blocking_reasons"]
    assert future["verified"] is False
    assert "source notion:bina was captured in the future" in future["blocking_reasons"]


def test_invalid_capture_timestamp_is_rejected():
    with pytest.raises(InventoryError, match="captured_at"):
        reconcile_inventory(
            {
                "inventory_question": "How many tasks remain?",
                "sources": [_source(captured_at="yesterday")],
            },
            now=NOW,
        )


def test_source_without_completeness_evidence_is_rejected():
    source = _source()
    source.pop("completeness_evidence")
    with pytest.raises(InventoryError, match="completeness_evidence"):
        reconcile_inventory(
            {"inventory_question": "How many tasks remain?", "sources": [source]},
            now=NOW,
        )
