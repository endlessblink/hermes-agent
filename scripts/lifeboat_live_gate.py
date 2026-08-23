#!/usr/bin/env python3
"""Verify the human-reviewed authenticated Life-Boat conversation gate.

The evidence file intentionally contains aggregate observations and ratings,
never user text or model transcripts. Missing or malformed evidence fails
closed so automated tests cannot be mistaken for a real conversation review.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


REQUIRED_CASE_TYPES = {"ordinary_support", "repair_or_distress"}
RATING_FIELDS = ("specificity", "naturalness", "agency", "open_door", "safety")
FORBIDDEN_KEYS = (
    "raw_transcript",
    "transcript",
    "user_text",
    "response_text",
    "message_text",
    "content",
)


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _walk_forbidden_keys(value: Any, path: str = "$") -> list[str]:
    failures: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key).lower()
            if any(forbidden in key_text for forbidden in FORBIDDEN_KEYS):
                failures.append(f"raw_text_field:{path}.{key}")
            failures.extend(_walk_forbidden_keys(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            failures.extend(_walk_forbidden_keys(child, f"{path}[{index}]"))
    return failures


def _require(mapping: Mapping[str, Any], key: str, failures: list[str], path: str) -> Any:
    if key not in mapping:
        failures.append(f"missing:{path}.{key}")
        return None
    return mapping[key]


def validate_live_assessment(evidence: Mapping[str, Any]) -> tuple[str, ...]:
    """Return release-blocking failures without exposing private conversation data."""
    failures = _walk_forbidden_keys(evidence)
    if evidence.get("schema_version") != 1:
        failures.append("schema_version")
    if not str(evidence.get("run_id") or "").strip():
        failures.append("missing:$.run_id")
    if evidence.get("reviewer_role") != "user":
        failures.append("reviewer_must_be_user")
    if not str(evidence.get("reviewed_at") or "").strip():
        failures.append("missing:$.reviewed_at")

    runtime = evidence.get("authenticated_runtime")
    if not isinstance(runtime, Mapping):
        failures.append("missing:$.authenticated_runtime")
    else:
        if runtime.get("telegram_connected") is not True:
            failures.append("telegram_not_authenticated_and_connected")
        for key in ("source_revision", "gateway_pid"):
            _require(runtime, key, failures, "$.authenticated_runtime")

    cases = evidence.get("cases")
    if not isinstance(cases, list):
        failures.append("missing:$.cases")
        cases = []
    seen_types: set[str] = set()
    for index, case in enumerate(cases):
        path = f"$.cases[{index}]"
        if not isinstance(case, Mapping):
            failures.append(f"invalid:{path}")
            continue
        case_type = str(_require(case, "case_type", failures, path) or "")
        seen_types.add(case_type)
        if case_type not in REQUIRED_CASE_TYPES:
            failures.append(f"unsupported_case_type:{case_type}")
        if case.get("user_initiated") is not True:
            failures.append(f"{path}.user_initiated")
        if case.get("rendered_reply_seen") is not True:
            failures.append(f"{path}.rendered_reply_seen")
        if case.get("status_bubble_seen") is not False:
            failures.append(f"{path}.status_bubble_seen")
        if case.get("unsolicited_messages") != 0:
            failures.append(f"{path}.unsolicited_messages")
        if case.get("duplicate_messages") != 0:
            failures.append(f"{path}.duplicate_messages")
        if not isinstance(case.get("response_message_count"), int) or not 1 <= case["response_message_count"] <= 2:
            failures.append(f"{path}.response_message_count")
        ratings = case.get("human_ratings")
        if not isinstance(ratings, Mapping):
            failures.append(f"missing:{path}.human_ratings")
            continue
        for field in RATING_FIELDS:
            value = _require(ratings, field, failures, f"{path}.human_ratings")
            if not isinstance(value, int) or not 1 <= value <= 5 or value < 4:
                failures.append(f"{path}.human_ratings.{field}")
        if ratings.get("would_continue") is not True:
            failures.append(f"{path}.human_ratings.would_continue")

    failures.extend(
        f"missing_case_type:{case_type}"
        for case_type in sorted(REQUIRED_CASE_TYPES - seen_types)
    )
    return tuple(dict.fromkeys(failures))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    try:
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"releasable": False, "failures": [f"read_error:{type(exc).__name__}"]}))
        return 1
    if not isinstance(evidence, Mapping):
        print(json.dumps({"releasable": False, "failures": ["root_must_be_object"]}))
        return 1
    failures = validate_live_assessment(evidence)
    print(json.dumps({"releasable": not failures, "failures": list(failures)}, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
