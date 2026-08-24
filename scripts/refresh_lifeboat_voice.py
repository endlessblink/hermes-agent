"""Refresh only untouched shipped Life-Boat voice files in the active runtime."""

from pathlib import Path

from gateway.lifeboat_voice import VOICE_DIR, ensure_voice_files


if __name__ == "__main__":
    ensure_voice_files()
    friend = (Path(VOICE_DIR) / "friend.md").read_text(encoding="utf-8")
    coach = (Path(VOICE_DIR) / "coach.md").read_text(encoding="utf-8")
    print(
        f"friend_voice_has_stock_opening={'stock opening' in friend} "
        f"friend_voice_has_old_checklist={'ask about all three in one sentence' in friend} "
        f"coach_voice_has_stock_opening={'stock opening' in coach} "
        f"coach_voice_has_old_checklist={'ask about all three in one sentence' in coach}"
    )
