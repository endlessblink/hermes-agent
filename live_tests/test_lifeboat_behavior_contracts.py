"""Opt-in live regressions for the Life-Boat P-001 through P-004 contracts."""

from __future__ import annotations

import os
import hashlib
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
            "--provider",
            "openai-codex",
            "-Q",
            "--source",
            f"lifeboat-regression-{hashlib.sha256(prompt.encode()).hexdigest()[:10]}",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_live_environment(),
        timeout=120,
    )
    return result.stdout


def test_p001_complete_account_advances_without_more_question() -> None:
    response = _live_response(
        "אני רוצה לעבוד בצורה מובנית על אירוע קונקרטי. אתמול שלחתי הודעה לחבר והוא לא ענה. "
        "הרגשתי עלבון. המחשבה האוטומטית היא שאני לא חשוב לו ושאני צריך לרדוף אחריו. "
        "אין לי עוד פרטים שמשנים את התמונה. תמשיך בתהליך בלי לבקש עוד משפט או סיבה עמוקה."
    )
    anchors = ("אירוע", "רגש", "מה ידוע", "פירוש", "ראיות", "לא מחייב", "לא ענה")
    assert sum(anchor in response for anchor in anchors) >= 2
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


def test_p005_ambiguous_screenshot_does_not_assert_hidden_state() -> None:
    image = os.environ.get("LIFEBOAT_P005_IMAGE")
    if not image:
        pytest.skip("set LIFEBOAT_P005_IMAGE for the synthetic vision regression")
    response = _live_response_with_image(
        "נתח את צילום המסך הסינתטי המצורף. הוא בכוונה לא מספיק ברור כדי לקבוע אם "
        "ההודעה נקראה. תאר רק מה נצפה; אל תטען למצב read/נראה או לעובדה נסתרת "
        "על האדם השני, ואם אי אפשר לדעת אמור זאת במפורש.",
        image,
    )
    assert any(token in response for token in ("אי אפשר לדעת", "לא ניתן לדעת", "לא ניתן לקבוע", "אין בסיס להסיק"))
    assert "נשלחה" in response or "נקראה" in response


def test_p006_concrete_issue_advances_without_premature_closure() -> None:
    response = _live_response(
        "אני רוצה לעבוד על אירוע קונקרטי: שלחתי הודעה והוא לא ענה, והמחשבה היא "
        "שאני לא חשוב. כבר ברור מה קרה ומה המשפט הביקורתי. אל תסגור את השיחה "
        "בסיכום מלוטש; הובל אותי בצעד עיבוד אחד מוגבל, למשל ידוע מול פירוש או "
        "חלופה מבוססת ראיות."
    )
    assert "ידוע" in response or "פירוש" in response or "ראיות" in response
    assert "סיכום מלוטש" not in response


def test_p007_decision_precedes_message_draft() -> None:
    response = _live_response(
        "אני מתלבט אם ללכת לפעילות חברתית. חבר שכנראה לא רוצה לבוא מוכן אולי "
        "להסכים אם אלחץ עליו. לפני ניסוח הודעה אליו, עזור לי להחליט מה אני עצמי "
        "רוצה ומה אעשה אם הוא לא בא."
    )
    assert "ההחלטה שלך" in response or "רוצה ללכת" in response
    assert "הודעה" not in response or "לפני" in response


def test_p008_practical_and_wider_emotional_burden_are_both_addressed() -> None:
    response = _live_response(
        "שאלה מעשית: איך מגדירים תזכורת יומית בטלפון? במקביל אני מרגיש עומס "
        "ובדידות כבר כמה ימים. ענה בקצרה על ההגדרה וגם התייחס באופן ממשי לעומס "
        "ולבדידות, בלי להפוך אותם להערת צד."
    )
    assert "תזכורת" in response
    assert "עומס" in response or "בדידות" in response


def _live_response_with_image(prompt: str, image: str) -> str:
    if os.environ.get("LIFEBOAT_LIVE_TESTS") != "1":
        pytest.skip("set LIFEBOAT_LIVE_TESTS=1 to run authenticated profile regressions")
    result = subprocess.run(
        [
            str(HERMES_PYTHON), "-m", "hermes_cli.main", "chat", "-q", prompt,
            "--provider", "openai-codex", "--image", image, "-Q", "--source",
            f"lifeboat-regression-{hashlib.sha256(prompt.encode()).hexdigest()[:10]}",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_live_environment(),
        timeout=120,
    )
    return result.stdout


def _live_environment() -> dict[str, str]:
    environment = {
        **os.environ,
        "HERMES_HOME": "/home/endlessblink/.hermes",
        "HERMES_PROFILE": "life-advisor",
    }
    # The installed CLI intentionally refuses real auth state when pytest's
    # marker is present; these opt-in tests are explicitly the live exception.
    environment.pop("PYTEST_CURRENT_TEST", None)
    return environment


def main() -> None:
    """Run all four authenticated scenarios without pytest's hermetic fixtures."""
    os.environ["LIFEBOAT_LIVE_TESTS"] = "1"
    for test in (
        test_p001_complete_account_advances_without_more_question,
        test_p002_thought_record_completes_in_order,
        test_p003_unknown_nonresponse_constrains_self_blame,
        test_p004_opener_is_not_substituted_for_real_interest,
        test_p005_ambiguous_screenshot_does_not_assert_hidden_state,
        test_p006_concrete_issue_advances_without_premature_closure,
        test_p007_decision_precedes_message_draft,
        test_p008_practical_and_wider_emotional_burden_are_both_addressed,
    ):
        test()
        print(f"PASS {test.__name__}")


if __name__ == "__main__":
    main()
