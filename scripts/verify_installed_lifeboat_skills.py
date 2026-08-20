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
    "P-015": "life-advisor-context-routing/SKILL.md",
    "P-016": "personal-emotional-coaching/references/mixed-technical-emotional-turns.md",
    "P-017": "personal-emotional-coaching/SKILL.md",
    "P-019": "professional-ecosystem/references/context-coverage-intake.md",
    "P-020": "professional-ecosystem/SKILL.md",
    "P-021": "professional-ecosystem/references/domain-intake-relevance-gate.md",
    "P-022": "professional-ecosystem/references/commercial-opportunity-gate.md",
}


def resolve_skill_root(profile_root: Path) -> Path:
    """Find the skill directory in flat and namespaced profile layouts."""
    candidates = (profile_root, profile_root / "productivity")
    for candidate in candidates:
        if (candidate / EXPECTED_FILES["P-005"]).is_file():
            return candidate
    return profile_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-root", type=Path, required=True)
    args = parser.parse_args(argv)
    skills_root = resolve_skill_root(args.skills_root)
    files = tuple(skills_root.rglob("*.md"))
    found = {item: (skills_root / relative).is_file() for item, relative in EXPECTED_FILES.items()}
    result = {
        "ok": all(found.values()),
        "checks": found,
        "markdownFiles": len(files),
        "skillsRoot": str(skills_root),
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
