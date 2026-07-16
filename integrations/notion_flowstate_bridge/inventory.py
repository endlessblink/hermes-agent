"""Pure proof gate for task inventories assembled from multiple systems."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


class InventoryError(ValueError):
    """The supplied inventory evidence does not satisfy the public contract."""


def _text(value: Any, field: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise InventoryError(f"{field} is required")
    if len(text) > limit:
        raise InventoryError(f"{field} may contain at most {limit} characters")
    return text


def _timestamp(value: Any, field: str) -> datetime:
    raw = _text(value, field, 100)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InventoryError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise InventoryError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def reconcile_inventory(
    args: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """Return exact counts only when every supplied evidence boundary is proven."""
    if not isinstance(args, dict):
        raise InventoryError("request must be an object")
    question = _text(args.get("inventory_question"), "inventory_question", 1000)
    sources = args.get("sources")
    if not isinstance(sources, list) or not sources:
        raise InventoryError("sources must be a non-empty list")
    if len(sources) > 20:
        raise InventoryError("sources may contain at most 20 entries")
    if len(json.dumps(sources, ensure_ascii=False).encode("utf-8")) > 524_288:
        raise InventoryError("sources payload may contain at most 524288 bytes")

    max_age = args.get("max_age_seconds", 300)
    if isinstance(max_age, bool) or not isinstance(max_age, int):
        raise InventoryError("max_age_seconds must be an integer")
    if not 30 <= max_age <= 3600:
        raise InventoryError("max_age_seconds must be between 30 and 3600")

    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        raise InventoryError("now must include a timezone")
    reference = reference.astimezone(timezone.utc)

    source_rows: list[dict[str, Any]] = []
    records: dict[str, dict[str, str]] = {}
    conflicts: list[dict[str, Any]] = []
    blocking: list[str] = []
    seen_sources: set[str] = set()
    unknown_count = 0
    missing_canonical = 0
    multi_source = len(sources) > 1
    capture_times: list[datetime] = []

    for source_index, raw_source in enumerate(sources):
        if not isinstance(raw_source, dict):
            raise InventoryError("each source must be an object")
        prefix = f"sources[{source_index}]"
        source_id = _text(raw_source.get("source_id"), f"{prefix}.source_id", 300)
        if source_id in seen_sources:
            raise InventoryError(f"duplicate source_id: {source_id}")
        seen_sources.add(source_id)
        scope = _text(raw_source.get("scope"), f"{prefix}.scope", 1000)
        completeness_evidence = _text(
            raw_source.get("completeness_evidence"),
            f"{prefix}.completeness_evidence",
            2000,
        )
        captured_raw = _text(
            raw_source.get("captured_at"), f"{prefix}.captured_at", 100
        )
        captured_at = _timestamp(captured_raw, f"{prefix}.captured_at")
        capture_times.append(captured_at)
        complete = raw_source.get("complete")
        if not isinstance(complete, bool):
            raise InventoryError(f"{prefix}.complete must be boolean")
        items = raw_source.get("items")
        if not isinstance(items, list):
            raise InventoryError(f"{prefix}.items must be a list")
        if len(items) > 500:
            raise InventoryError(f"source {source_id} may contain at most 500 items")

        if not complete:
            blocking.append(f"source {source_id} is partial")
        age = (reference - captured_at).total_seconds()
        if age < -30:
            blocking.append(f"source {source_id} was captured in the future")
        elif age > max_age:
            blocking.append(f"source {source_id} is stale")

        seen_item_ids: set[str] = set()
        source_unknown = 0
        source_uncharacterized = 0
        for item_index, raw_item in enumerate(items):
            if not isinstance(raw_item, dict):
                raise InventoryError(f"source {source_id} items must be objects")
            item_prefix = f"{prefix}.items[{item_index}]"
            item_id = _text(raw_item.get("id"), f"{item_prefix}.id", 500)
            if item_id in seen_item_ids:
                raise InventoryError(
                    f"source {source_id} contains duplicate item id {item_id}"
                )
            seen_item_ids.add(item_id)
            title = _text(raw_item.get("title"), f"{item_prefix}.title", 1000)
            evidence = _text(
                raw_item.get("evidence"), f"{item_prefix}.evidence", 2000
            )
            classification = _text(
                raw_item.get("classification"), f"{item_prefix}.classification", 30
            )
            if classification not in {
                "characterized",
                "uncharacterized",
                "unknown",
            }:
                raise InventoryError(
                    "classification must be characterized, uncharacterized, or unknown"
                )
            canonical_id = str(raw_item.get("canonical_id") or "").strip()
            if len(canonical_id) > 500:
                raise InventoryError(
                    f"{item_prefix}.canonical_id may contain at most 500 characters"
                )
            if multi_source and not canonical_id:
                missing_canonical += 1

            if classification == "unknown":
                source_unknown += 1
                unknown_count += 1
            elif classification == "uncharacterized":
                source_uncharacterized += 1

            key = canonical_id or f"{source_id}:{item_id}"
            candidate = {
                "canonical_id": key,
                "classification": classification,
                "source_id": source_id,
                "item_id": item_id,
                "title": title,
                "evidence": evidence,
            }
            existing = records.get(key)
            if existing is None:
                records[key] = candidate
            elif existing["classification"] != classification:
                conflicts.append(
                    {
                        "canonical_id": key,
                        "classifications": [
                            existing["classification"],
                            classification,
                        ],
                        "sources": [existing["source_id"], source_id],
                    }
                )

        source_rows.append(
            {
                "source_id": source_id,
                "scope": scope,
                "captured_at": captured_raw,
                "complete": complete,
                "completeness_evidence": completeness_evidence,
                "observed_total": len(items),
                "observed_uncharacterized": source_uncharacterized,
                "unknown": source_unknown,
            }
        )

    if missing_canonical:
        noun = "item lacks" if missing_canonical == 1 else "items lack"
        blocking.append(f"{missing_canonical} cross-source {noun} canonical_id")
    if multi_source and (max(capture_times) - min(capture_times)).total_seconds() > 120:
        blocking.append("source capture times differ by more than 120 seconds")
    if unknown_count:
        noun = "item has" if unknown_count == 1 else "items have"
        blocking.append(f"{unknown_count} {noun} unknown characterization")
    if conflicts:
        noun = "item has" if len(conflicts) == 1 else "items have"
        blocking.append(
            f"{len(conflicts)} canonical {noun} conflicting classifications"
        )

    verified = not blocking
    uncharacterized = sum(
        item["classification"] == "uncharacterized" for item in records.values()
    )
    return {
        "inventory_question": question,
        "verified": verified,
        "exact_total": len(records) if verified else None,
        "exact_uncharacterized": uncharacterized if verified else None,
        "observed_total": len(records),
        "observed_uncharacterized": uncharacterized,
        "sources": source_rows,
        "conflicts": conflicts,
        "blocking_reasons": blocking,
    }


__all__ = ["InventoryError", "reconcile_inventory"]
