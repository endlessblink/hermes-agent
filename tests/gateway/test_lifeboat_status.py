from gateway.lifeboat_status import format_lifeboat_technical_status


def test_queue_only_keeps_queue_notice():
    assert format_lifeboat_technical_status("Queued for processing") == "התור נקלט; העיבוד יתחיל בקרוב."


def test_restart_only_is_the_only_blocker():
    rendered = format_lifeboat_technical_status("Recovery remains open; restart required")
    assert "חסום\nנדרשת הפעלה מחדש בלבד." in rendered
    assert "Telegram" not in rendered


def test_telegram_verification_only_final_delivery_is_completed():
    rendered = format_lifeboat_technical_status("Final delivered Telegram verification")
    assert "אימות Telegram הושלם במסירת ההודעה הזו." in rendered
    assert "חסום" not in rendered
    assert "הצעד הבא" not in rendered
    assert "התור נקלט" not in rendered


def test_mixed_queue_completed_and_verification_prefers_final_delivery():
    rendered = format_lifeboat_technical_status(
        "Queued recovery completed; final delivered Telegram verification"
    )
    assert "אימות Telegram הושלם במסירת ההודעה הזו." in rendered
    assert "התור נקלט" not in rendered
    assert "חסום" not in rendered
    assert "הצעד הבא" not in rendered
