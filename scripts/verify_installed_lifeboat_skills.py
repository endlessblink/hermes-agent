#!/usr/bin/env python3
"""Verify privacy-safe Life-Boat skill safeguards in the installed profile."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


EXPECTED_FILES = {
    "P-005": "personal-emotional-coaching/references/2026-08-17-topic-queue-and-bully-work.md",
    "P-006": "personal-emotional-coaching/SKILL.md",
    "P-007": "personal-emotional-coaching/references/2026-08-18-decision-support-and-subtext.md",
    "P-008": "personal-emotional-coaching/references/mixed-technical-emotional-turns.md",
    "P-014": "flow-state/references/portfolio-review-decision-surface.md",
    "P-015": "personal-emotional-coaching/SKILL.md",
    "P-016": "personal-emotional-coaching/references/mixed-technical-emotional-turns.md",
    "P-017": "personal-emotional-coaching/SKILL.md",
    "P-019": "professional-ecosystem/references/context-coverage-intake.md",
    "P-020": "professional-ecosystem/SKILL.md",
    "P-021": "professional-ecosystem/references/domain-intake-relevance-gate.md",
    "P-022": "professional-ecosystem/references/commercial-opportunity-gate.md",
}

# File presence catches a broken install, while these small content contracts
# catch a stale or replaced file that happens to keep the same path.  The
# phrases are deliberately broad policy anchors, not a transcript oracle.
EXPECTED_CONTENT = {
    "P-005": ("do not infer hidden states", "observable", "evidence"),
    "P-006": ("advance one bounded processing step", "pre-send anti-closure check"),
    "P-007": ("decide with noam before drafting", "reluctant cooperation"),
    "P-008": ("technical task", "same response", "personal/emotional point"),
    "P-014": ("maximum three", "mutation", "apply nothing before approval"),
    "P-015": ("canonical queue", "continue its sole `active` item", "keep exactly one item `active`"),
    "P-016": ("preserve every explicitly raised personal/emotional point", "same response"),
    "P-017": ("explicit decline", "silence is ambiguous", "personal channel"),
    "P-019": ("one concrete question", "unknown", "no domain-level or portfolio-level conclusion"),
    "P-020": ("one small, concrete question", "one professional domain", "never ask noam to enumerate"),
    "P-021": ("stop and move to the next lane", "sufficiently mapped"),
    "P-022": ("commercially unvalidated", "reachable audience", "first-payment path"),
}


def resolve_skill_root(profile_root: Path) -> Path:
    """Find the skill directory in flat and namespaced profile layouts."""
    candidates = (profile_root, profile_root / "productivity")
    for candidate in candidates:
        if (candidate / EXPECTED_FILES["P-005"]).is_file():
            return candidate
    return profile_root


def content_contracts_match(skills_root: Path) -> dict[str, bool]:
    """Check policy anchors without retaining or printing skill text."""
    result: dict[str, bool] = {}
    for item, relative in EXPECTED_FILES.items():
        try:
            text = (skills_root / relative).read_text(encoding="utf-8").lower()
        except OSError:
            text = ""
        result[item] = all(anchor.lower() in text for anchor in EXPECTED_CONTENT[item])
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-root", type=Path, required=True)
    args = parser.parse_args(argv)
    skills_root = resolve_skill_root(args.skills_root)
    files = tuple(skills_root.rglob("*.md"))
    found = {item: (skills_root / relative).is_file() for item, relative in EXPECTED_FILES.items()}
    content = content_contracts_match(skills_root)
    result = {
        "ok": all(found.values()) and all(content.values()),
        "checks": found,
        "contentChecks": content,
        "markdownFiles": len(files),
        "skillsRoot": str(skills_root),
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
