#!/usr/bin/env python3
"""Inventory the Life-Boat system and report anything nothing is talking to.

Run it after any change that adds or removes a scheduled job, a note the bot
keeps, or a consumer of one. It reads only; it never writes to the vault.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from gateway.lifeboat_connections import Artifact, audit_connections  # noqa: E402


HOME = pathlib.Path.home() / ".hermes"
VAULT = pathlib.Path(
    "/media/endlessblink/data/app-data/sync/Dropbox/OBSIDIAN_SYNCED/MAIN VULT"
)
JOURNAL = VAULT / "_System/Hermes Knowledge Graph/Projects/Daily Evidence Journal"
PROJECTS = VAULT / "_System/Hermes Knowledge Graph/Projects"


def _jobs():
    jobs = []
    for store in (HOME / "cron/jobs.json",
                  HOME / "profiles/life-advisor/cron/jobs.json"):
        if store.is_file():
            jobs.extend(json.loads(store.read_text(encoding="utf-8")).get("jobs", []))
    return [j for j in jobs if isinstance(j, dict)]


def _producers(needle, jobs):
    """Jobs whose prompt names this artifact -- i.e. that write it."""
    return tuple(
        str(j.get("name"))
        for j in jobs
        if j.get("enabled") and needle in json.dumps(j, ensure_ascii=False)
    )


def _consumers(needle):
    """Skills that mention this artifact, so a turn loading them can use it."""
    roots = [HOME / "profiles/life-advisor/skills"]
    found = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.md"):
            try:
                if needle in path.read_text(encoding="utf-8", errors="replace"):
                    found.append(path.parent.name)
            except OSError:
                continue
    return tuple(sorted(set(found)))


def main() -> int:
    jobs = _jobs()

    artifacts = [
        Artifact(
            "Daily Evidence Journal",
            exists=JOURNAL.is_dir() and any(JOURNAL.glob("2*.md")),
            produced_by=_producers("Daily Evidence Journal", jobs),
            consumed_by=_consumers("Daily Evidence Journal"),
        ),
        Artifact(
            "Weekly rollup",
            exists=(JOURNAL / "Weekly").is_dir() and any((JOURNAL / "Weekly").glob("*.md")),
            produced_by=_producers("Weekly", jobs),
            consumed_by=_consumers("Weekly"),
        ),
        Artifact("Monthly rollup", exists=(JOURNAL / "Monthly").is_dir()),
        Artifact("Quarterly rollup", exists=(JOURNAL / "Quarterly").is_dir()),
        Artifact("Yearly rollup", exists=(JOURNAL / "Yearly").is_dir()),
        Artifact(
            "Emotional Processing Queue",
            exists=(PROJECTS / "Emotional Processing Queue.md").is_file(),
            produced_by=_producers("Emotional Processing Queue", jobs),
            consumed_by=_consumers("Emotional Processing Queue"),
        ),
        Artifact(
            "Emotional Life and Patterns",
            exists=(PROJECTS / "Project - Emotional Life and Patterns.md").is_file(),
            produced_by=_producers("Emotional Life and Patterns", jobs),
            consumed_by=_consumers("Emotional Life and Patterns"),
        ),
    ]

    findings = audit_connections(artifacts)

    print(f"Life-Boat connection audit — {len(artifacts)} artifacts, {len(findings)} findings\n")
    for artifact in artifacts:
        state = "OK      " if not any(f.artifact == artifact.name for f in findings) else "FINDING "
        print(f"{state}{artifact.name}")
        print(f"          produced by: {', '.join(artifact.produced_by) or '(nothing)'}")
        print(f"          read by:     {', '.join(artifact.consumed_by) or '(nothing)'}")
    print()
    for finding in findings:
        for reason in finding.reasons:
            print(f"FINDING  {finding.artifact}: {reason}")

    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
