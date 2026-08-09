"""Verify the installed Life-Boat psychology layer and privacy boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

from gateway.lifeboat_psychology import (
    build_signal_guidance,
    classify_lifeboat_signals,
    record_lifeboat_trajectory,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    source_root = Path(__file__).resolve().parents[1]
    installed_root = Path("/home/endlessblink/.hermes/hermes-agent")
    relative_files = (
        Path("gateway/run.py"),
        Path("gateway/lifeboat_followups.py"),
        Path("gateway/lifeboat_psychology.py"),
    )
    for relative in relative_files:
        source = source_root / relative
        installed = installed_root / relative
        if not installed.is_file() or _sha256(source) != _sha256(installed):
            print(f"FAIL source/installed mismatch: {relative}")
            return 1

    cases = {
        "loop": "אני תקוע בלופ של ביקורת עצמית",
        "depressive": "I feel hopeless and have no energy",
        "crisis": "I might hurt myself tonight",
    }
    signals = {name: classify_lifeboat_signals(text) for name, text in cases.items()}
    if not signals["loop"].thought_loop or not signals["loop"].self_criticism:
        print("FAIL Hebrew loop/self-criticism routing")
        return 1
    if not signals["depressive"].depressive_thoughts or not signals["crisis"].possible_crisis:
        print("FAIL depressive/crisis routing")
        return 1

    with tempfile.TemporaryDirectory() as directory:
        profile_home = Path(directory)
        trajectory = record_lifeboat_trajectory(
            profile_home,
            "verification-session",
            cases["crisis"],
        )
        guidance = build_signal_guidance("yes", trajectory)
        state = json.loads(
            (profile_home / "state" / "lifeboat-psychology.json").read_text(
                encoding="utf-8"
            )
        )
        serialized = json.dumps(state, ensure_ascii=False)
        if "I might hurt myself tonight" in serialized:
            print("FAIL raw user text persisted in trajectory state")
            return 1
        if "safe right now" not in guidance:
            print("FAIL recent safety context was not carried into the next turn")
            return 1

    print("Life-Boat psychology source/installed, routing, trajectory, and privacy checks verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
