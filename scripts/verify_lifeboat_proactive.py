"""Verify the source and installed gateway share the Life-Boat integration."""

from pathlib import Path


REQUIRED_MARKERS = (
    "arm_lifeboat_prompts",
    "Life-Boat proactive scheduling followup=",
)


def main() -> int:
    source_root = Path(__file__).resolve().parents[1]
    installed_root = Path("/home/endlessblink/.hermes/hermes-agent")
    files = {
        "source gateway": source_root / "gateway" / "run.py",
        "installed gateway": installed_root / "gateway" / "run.py",
        "source scheduler": source_root / "gateway" / "lifeboat_followups.py",
        "installed scheduler": installed_root / "gateway" / "lifeboat_followups.py",
    }
    failed = False
    for label, path in files.items():
        if not path.is_file():
            print(f"FAIL {label}: missing")
            failed = True
            continue
        text = path.read_text(encoding="utf-8")
        markers = REQUIRED_MARKERS if path.name == "run.py" else ("def arm_lifeboat_prompts(", "ACHIEVEMENT_DELAY")
        for marker in markers:
            if marker not in text:
                print(f"FAIL {label}: missing {marker}")
                failed = True
    if failed:
        return 1
    print("Life-Boat proactive source/installed integration verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
