#!/usr/bin/env python3
"""Read every review-log pattern back against what actually enforces it.

The 2026-08-21 classification in the Obsidian log assigned each pattern to a
canonical task. That answers who owns it, not whether anything stops it. This
answers the second question: for each pattern, either name the check that runs
at delivery time and prove the symbol resolves, or say plainly that the pattern
is evidence for a human to read and not a thing code can decide.

Run it after changing any Life-Boat gate. A pattern claiming an enforcer that
no longer exists fails here.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path


REVIEW_LOG = Path(
    "/media/endlessblink/data/app-data/sync/Dropbox/OBSIDIAN_SYNCED/MAIN VULT"
    "/_System/Hermes Governance/Life-Boat Response Pattern Review Log.md"
)

#: pattern -> (module, symbol) of the check that decides it at delivery time.
ENFORCED: dict[str, tuple[str, str]] = {
    "P-001": ("gateway.lifeboat_reviewer", "_QUESTION_LOOP_RE"),
    "P-004": ("gateway.lifeboat_reviewer", "_ABSTRACT_STAGE_RE"),
    "P-005": ("gateway.lifeboat_reviewer", "_OTHERS_MIND_RE"),
    "P-006": ("gateway.lifeboat_contracts", "_CLOSURE_RE"),
    "P-007": ("gateway.lifeboat_reviewer", "_DECISION_OFFLOAD_RE"),
    "P-014": ("gateway.lifeboat_contracts", "_STRUCTURE_RE"),
    "P-019": ("gateway.lifeboat_reviewer", "_SUPPORT_MENU_RE"),
    "P-020": ("gateway.lifeboat_reviewer", "_CAPACITY_SURVEY_RE"),
    "P-023": ("gateway.lifeboat_reviewer", "_AFFECT_WORD_RE"),
    "P-024": ("gateway.lifeboat_reentry", "_GENERIC_OPENER_RE"),
    "P-025": ("gateway.lifeboat_psychology", "classify_lifeboat_signals"),
    "P-026": ("gateway.lifeboat_contracts", "contract_violations"),
}

#: pattern -> why no check decides it. Each of these needs a person reading a
#: transcript, or belongs to a system outside the gateway.
NOT_CODE_DECIDABLE: dict[str, str] = {
    "P-002": "whether the agreed sequence was abandoned depends on what was agreed",
    "P-003": "deciding whose claim is left standing needs a judgement about the claim",
    "P-008": "whether the wider burden was dropped requires knowing what it was",
    "P-009": "platform-specific recovery advice; correctness is external to the reply",
    "P-010": "Telegram form rendering — a client-side bug, not a reply shape",
    "P-011": "the approval boundary is a policy question, not a text property",
    "P-012": "persistence claims are verified against Flow State, not the draft",
    "P-013": "a Flow State connector limitation",
    "P-015": "which thread to resume depends on the canonical queue's contents",
    "P-016": "whether a co-mentioned topic was dropped needs the whole turn's context",
    "P-017": "an evening invitation is a scheduling decision",
    "P-018": "live Flow State timer awareness",
    "P-021": "relevance drift needs a reader who knows the goal",
    "P-022": "commercial claims need evidence the gateway does not hold",
}

_HEADING_RE = re.compile(r"^### (P-\d{3}) — ", re.M)


def run() -> list[str]:
    problems: list[str] = []

    for pattern, (module_name, symbol) in sorted(ENFORCED.items()):
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            problems.append(f"{pattern}: cannot import {module_name} ({exc})")
            continue
        if not hasattr(module, symbol):
            problems.append(f"{pattern}: {module_name} has no {symbol}")

    if REVIEW_LOG.exists():
        found = set(_HEADING_RE.findall(REVIEW_LOG.read_text(encoding="utf-8")))
        accounted = set(ENFORCED) | set(NOT_CODE_DECIDABLE)
        for pattern in sorted(found - accounted):
            problems.append(f"{pattern}: in the review log, in neither list here")
    else:
        problems.append(f"review log not readable at {REVIEW_LOG}")

    return problems


def main() -> int:
    problems = run()
    print(
        f"review-log coverage: {len(ENFORCED)} patterns enforced by a running check, "
        f"{len(NOT_CODE_DECIDABLE)} need a human reader"
    )
    for line in problems:
        print(f"  FAIL {line}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
