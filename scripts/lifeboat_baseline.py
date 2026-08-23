#!/usr/bin/env python3
"""Freeze the Life-Boat gate files, and put them back.

A release gate needs something to fall back to. "Revert the commit" is not that
when the thing that decides behaviour is a separate installed tree that the
repository does not control -- and it was exactly that gap between the two
trees that let a gate sit installed with no caller for weeks.

So the baseline is a content snapshot of the files that decide what the user
reads, taken from the installed runtime, with a sha256 per file. Freezing
records what is running now; restoring puts those bytes back and reports every
file it changed. Restoring does not restart the gateway -- that stays a
deliberate act.

    freeze [--label TEXT]   snapshot the installed gate files
    verify                  compare the installed tree against the baseline
    restore [--dry-run]     put the baseline bytes back
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path


INSTALLED_ROOT = Path("/home/endlessblink/.hermes/hermes-agent")
BASELINE_DIR = Path(__file__).resolve().parent.parent / "baselines" / "lifeboat"
MANIFEST = BASELINE_DIR / "manifest.json"

#: The files that decide what reaches the Telegram topic. Not the whole
#: gateway -- a baseline nobody can read is a baseline nobody checks.
GATE_FILES = (
    "gateway/lifeboat_surface.py",
    "gateway/lifeboat_reviewer.py",
    "gateway/lifeboat_rewrite.py",
    "gateway/lifeboat_editor.py",
    "gateway/lifeboat_reentry.py",
    "gateway/lifeboat_contracts.py",
    "gateway/lifeboat_modes.py",
    "gateway/lifeboat_psychology.py",
    "gateway/lifeboat_runtime_check.py",
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(root: Path, baseline: Path, label: str) -> dict:
    files: dict[str, str] = {}
    baseline.mkdir(parents=True, exist_ok=True)
    for rel in GATE_FILES:
        source = root / rel
        if not source.exists():
            raise FileNotFoundError(f"gate file missing from the runtime: {rel}")
        target = baseline / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        files[rel] = _digest(source)
    manifest = {"label": label, "root": str(root), "files": files}
    (baseline / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def _load(baseline: Path) -> dict:
    path = baseline / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"no baseline frozen at {baseline}")
    return json.loads(path.read_text(encoding="utf-8"))


def verify(root: Path, baseline: Path) -> list[str]:
    """Return one line per file that differs from the baseline."""
    manifest = _load(baseline)
    drifted: list[str] = []
    for rel, expected in sorted(manifest["files"].items()):
        installed = root / rel
        if not installed.exists():
            drifted.append(f"{rel}: missing from the runtime")
        elif _digest(installed) != expected:
            drifted.append(f"{rel}: differs from the baseline")
    return drifted


def restore(root: Path, baseline: Path, *, dry_run: bool = False) -> list[str]:
    """Put the baseline bytes back; return the files that changed."""
    manifest = _load(baseline)
    changed: list[str] = []
    for rel, expected in sorted(manifest["files"].items()):
        installed = root / rel
        if installed.exists() and _digest(installed) == expected:
            continue
        changed.append(rel)
        if not dry_run:
            installed.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(baseline / rel, installed)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze_p = sub.add_parser("freeze", help="snapshot the installed gate files")
    freeze_p.add_argument("--label", default="", help="what this baseline is")
    sub.add_parser("verify", help="compare the runtime against the baseline")
    restore_p = sub.add_parser("restore", help="put the baseline bytes back")
    restore_p.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "freeze":
        manifest = freeze(INSTALLED_ROOT, BASELINE_DIR, args.label)
        print(f"froze {len(manifest['files'])} gate files: {args.label or 'unlabelled'}")
        return 0

    if args.command == "verify":
        drifted = verify(INSTALLED_ROOT, BASELINE_DIR)
        print(f"baseline: {_load(BASELINE_DIR)['label'] or 'unlabelled'}")
        for line in drifted:
            print(f"  drift {line}")
        print("runtime matches the baseline" if not drifted else f"{len(drifted)} file(s) drifted")
        return 1 if drifted else 0

    changed = restore(INSTALLED_ROOT, BASELINE_DIR, dry_run=args.dry_run)
    verb = "would restore" if args.dry_run else "restored"
    print(f"{verb} {len(changed)} file(s)")
    for rel in changed:
        print(f"  {rel}")
    if changed and not args.dry_run:
        print("the gateway still needs a restart to load them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
