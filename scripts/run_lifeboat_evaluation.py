"""Run the privacy-safe Life-Boat transcript oracle against a JSON replay.

The input may contain synthetic or approved review transcripts.  The report only
contains aggregate metrics and failure tags; it never prints response text.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from gateway.lifeboat_evaluation import (
    aggregate_metrics,
    build_multiturn_gold_scenarios,
    evaluate_transcript,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, help="JSON replay file; response text is never printed")
    parser.add_argument("--shape", action="store_true", help="check and print only the gold-set shape")
    args = parser.parse_args(argv)
    scenarios = build_multiturn_gold_scenarios()
    if args.shape:
        print(json.dumps({"scenarios": len(scenarios)}, sort_keys=True))
        return 0 if len(scenarios) == 60 else 1
    if args.input is None:
        parser.error("--input is required unless --shape is used")
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("replay JSON must be a list")
    by_id = {item.scenario.scenario_id: item for item in scenarios}
    results = []
    for item in payload:
        if not isinstance(item, dict) or item.get("scenario_id") not in by_id:
            raise ValueError("replay contains an unknown scenario")
        responses = item.get("responses")
        if not isinstance(responses, list):
            raise ValueError("each replay item needs a responses list")
        results.append(evaluate_transcript(by_id[item["scenario_id"]], responses))
    metrics = aggregate_metrics(results)
    print(json.dumps(metrics, sort_keys=True))
    return 0 if metrics["failed_scenarios"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
