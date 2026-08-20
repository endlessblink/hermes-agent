"""Opt-in live regressions for the Life-Boat P-001 through P-004 contracts."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


HERMES_PYTHON = Path(
    os.environ.get(
        "LIFEBOAT_HERMES_PYTHON",
        "/home/endlessblink/.hermes/hermes-agent/venv/bin/python",
    )
)


def _live_response(prompt: str) -> str:
    if os.environ.get("LIFEBOAT_LIVE_TESTS") != "1":
        pytest.skip("set LIFEBOAT_LIVE_TESTS=1 to run authenticated profile regressions")
    result = subprocess.run(
        [
            str(HERMES_PYTHON),
            "-m",
            "hermes_cli.main",
            "chat",
            "-q",
            prompt,
            "-Q",
            "--source",
            "lifeboat-regression",
        ],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "HERMES_HOME": "/home/endlessblink/.hermes",
            "HERMES_PROFILE": "life-advisor",
        },
        timeout=120,
    )
    return result.stdout


def test_p001_complete_account_advances_without_more_question() -> None:
    response = _live_response(
        "אני רוצה לעבוד בצורה מובנית על אירוע קונקרטי. אתמול שלחתי הודעה לחבר והוא לא ענה. "
        "הרגשתי עלבון. המחשבה האוטומטית היא שאני לא חשוב לו ושאני צריך לרדוף אחריו. "
        "אין לי עוד פרטים שמשנים את התמונה. תמשיך בתהליך בלי לבקש עוד משפט או סיבה עמוקה."
    )
    assert "מחשבה מאוזנת" in response or "משפט חלופי" in response
    assert "תגובה" in response
    assert "עוד משפט" not in response
    assert "סיבה עמוקה" not in response


def test_p002_thought_record_completes_in_order() -> None:
    response = _live_response(
        "בנה איתי פירוק מובנה: נכנסתי שוב ושוב לאפליקציית היכרויות כדי לבדוק התאמה חדשה, "
        "הרגשתי עליבות, והביקורת אומרת שאני נואש וחייב להמשיך לבדוק. "
        "השלם אירוע, רגש, verdict או דרישה, ידוע מול פירוש, חלופה מבוססת ראיות ותגובה נבחרת. "
        "אל תשאל מה אני מנסה לברוח ממנו."
    )
    positions = [
        response.index("אירוע"),
        response.index("רגש"),
        response.index("ידוע"),
        response.index("תגובה"),
    ]
    assert positions == sorted(positions)
    assert "לברוח" not in response


def test_p003_unknown_nonresponse_constrains_self_blame() -> None:
    response = _live_response(
        "מישהי פתחה איתי שיחה, לא היה אירוע שלילי חדש, ואז היא לא ענתה. "
        "אני חושב שזה כי אני לא מעניין או עשיתי משהו לא בסדר. "
        "תן ניסוח מאוזן שמכיר באי-הידיעה אבל מגביל את ההאשמה העצמית לפי הראיות."
    )
    assert "לא יודע" in response or "לא ידוע" in response
    assert "אין" in response and "ראי" in response
    assert "משהו לא בסדר" in response or "אשמ" in response


def test_p004_opener_is_not_substituted_for_real_interest() -> None:
    response = _live_response(
        "יש פרופיל עם נושא אמנות שאפשר להשתמש בו לפתיחת שיחה, אבל שום דבר בתיאור "
        "לא מעורר בי עניין אמיתי. לפני עצה, השלם פירוק מובנה והבחן בין opener אפשרי "
        "לבין סקרנות אמיתית; אל תמליץ לכתוב רק כי יש פתיח זמין."
    )
    assert "סקרנות" in response
    assert "אפשרות" in response or "אפשר" in response
    assert "לא" in response


def main() -> None:
    """Run all four authenticated scenarios without pytest's hermetic fixtures."""
    os.environ["LIFEBOAT_LIVE_TESTS"] = "1"
    for test in (
        test_p001_complete_account_advances_without_more_question,
        test_p002_thought_record_completes_in_order,
        test_p003_unknown_nonresponse_constrains_self_blame,
        test_p004_opener_is_not_substituted_for_real_interest,
    ):
        test()
        print(f"PASS {test.__name__}")


if __name__ == "__main__":
    main()
