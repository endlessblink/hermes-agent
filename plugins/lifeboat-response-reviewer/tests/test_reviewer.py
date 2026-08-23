import time
from types import SimpleNamespace

import reviewer as reviewer_module
from reviewer import Reviewer, Settings, classify, is_emotional_scope


def test_speed_dating_failure_and_meta_corrections_are_flagged():
    text = "זה נשמע מבאס, אבל אולי יש בך נכונות לתת לעצמך עוד הזדמנויות — כל הכבוד. זה לא אומר שאתה לא בסדר. עדכנתי את הכלל."
    report = classify(text)
    assert report.flagged
    assert {"amplified_negative_affect", "praise_growth_assignment", "unsolicited_reassurance", "operational_receipt"} <= set(report.reasons)


def test_normal_work_and_mapping_are_untouched():
    assert not is_emotional_scope("מיפיתי את המשימות לפרויקט והוספתי תאריכים", "life-advisor")
    assert not classify("מיפיתי את המשימות לפרויקט והוספתי תאריכים.") .flagged
    assert not classify("The mapping is complete. I added the dates and owners.").flagged


def test_hebrew_and_english_risks():
    assert classify("זה לא אומר שאתה שבור. אתה אמיץ, והכול יהיה בסדר.").flagged
    assert classify("That sounds painful. You are resilient and everything will be okay.").flagged


def test_closure_and_forced_binary_are_flagged():
    assert "polished_closure" in classify("אולי יש בך נכונות לתת לעצמך עוד הזדמנויות.").reasons
    assert "forced_binary_question" in classify("רוצה להמשיך או לעצור, כן או לא?").reasons


def test_operational_receipt_requires_emotional_context():
    assert "operational_receipt" not in classify("עדכנתי את המשימה והוספתי את התאריך.").reasons
    assert "operational_receipt" in classify("זה כואב, ועדכנתי את הכלל.").reasons


def test_live_scope_is_profile_local():
    assert is_emotional_scope("אני מרגיש דחוי ורוצה להבין מה קרה", "life-advisor")
    assert not is_emotional_scope("אני מרגיש דחוי ורוצה להבין מה קרה", "default")


def test_recursion_prevention_and_fail_open():
    calls = []
    holder = {}

    def llm():
        class Llm:
            def complete(self, **kwargs):
                calls.append(kwargs)
                nested = holder["reviewer"].transform("זה לא אומר שאתה שבור.", session_id="s", profile_name="life-advisor", settings=Settings())
                assert nested == ""
                return SimpleNamespace(text="נשאר עם הפרט עצמו — מה היה החלק הכי לא נעים?")
        return Llm()

    holder["reviewer"] = Reviewer(llm)
    reviewer = holder["reviewer"]
    reviewer.mark_scope("s", True)
    result = reviewer.transform("זה לא אומר שאתה שבור.", session_id="s", profile_name="life-advisor", settings=Settings())
    assert result.startswith("נשאר")
    assert len(calls) == 1


def test_timeout_and_reviewer_failure_leave_original_untouched():
    def broken():
        raise TimeoutError("bounded")

    reviewer = Reviewer(broken)
    reviewer.mark_scope("s", True)
    assert reviewer.transform("זה לא אומר שאתה שבור.", session_id="s", profile_name="life-advisor", settings=Settings()) == ""


def test_dry_run_and_unflagged_skip_model():
    calls = []
    reviewer = Reviewer(lambda: calls.append(1))
    reviewer.mark_scope("s", True)
    assert reviewer.transform("הנה התשובה העובדתית.", session_id="s", profile_name="life-advisor", settings=Settings()) == ""
    assert reviewer.transform("זה לא אומר שאתה שבור.", session_id="s", profile_name="life-advisor", settings=Settings(dry_run=True)) == ""
    assert not calls


def test_classifier_latency_benchmark():
    sample = "זה נשמע מבאס, אבל אין סיבה לדאוג. אתה אמיץ, והכול יהיה בסדר."
    start = time.perf_counter()
    for _ in range(10000):
        classify(sample)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 500


def test_register_uses_current_plugin_context_properties(monkeypatch):
    hooks = {}

    class FakeLlm:
        def complete(self, **kwargs):
            return SimpleNamespace(text="מה בתוך הרגע הזה עדיין לא קיבל מקום?")

    class FakeCtx:
        profile_name = "life-advisor"
        llm = FakeLlm()

        def register_hook(self, name, callback):
            hooks[name] = callback

    monkeypatch.setattr(reviewer_module.Settings, "from_host", classmethod(lambda cls: Settings()))
    reviewer_module.register(FakeCtx())
    hooks["pre_llm_call"](session_id="s", user_message="הרגשתי דחוי בדייט")
    result = hooks["transform_llm_output"](
        session_id="s",
        response_text="זה לא אומר שאתה שבור.",
        model="test",
        platform="test",
    )
    assert result.startswith("מה בתוך")
