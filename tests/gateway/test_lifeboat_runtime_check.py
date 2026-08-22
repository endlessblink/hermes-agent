"""A startup self-check for the Life-Boat delivery path.

On 2026-08-22 the installed classifier was missing two functions the delivery
gate imported. Every gate was wrapped in a try/except, so the failure was
invisible: the gates simply stopped applying and the bot kept talking. Nothing
reported it, and the tests were green the whole time because the tests ran
against a different copy of the code.

The check exists so that a runtime which cannot enforce the gates says so
loudly instead of failing open in silence.
"""

from __future__ import annotations

import pytest

from gateway.lifeboat_runtime_check import (
    REQUIRED_SYMBOLS,
    lifeboat_runtime_problems,
)


def test_a_healthy_runtime_reports_no_problems() -> None:
    assert lifeboat_runtime_problems() == ()


def test_the_check_covers_the_modules_the_gate_depends_on() -> None:
    covered = set(REQUIRED_SYMBOLS)

    assert "gateway.lifeboat_psychology" in covered
    assert "gateway.lifeboat_surface" in covered
    assert "gateway.lifeboat_modes" in covered
    assert "gateway.lifeboat_contracts" in covered


def test_the_exact_missing_function_from_the_incident_is_covered() -> None:
    """This is the symbol whose absence disabled every gate in production."""
    assert "record_lifeboat_response_fingerprint" in REQUIRED_SYMBOLS["gateway.lifeboat_psychology"]


def test_a_missing_symbol_is_reported(monkeypatch) -> None:
    import gateway.lifeboat_psychology as psychology

    monkeypatch.delattr(psychology, "record_lifeboat_response_fingerprint")

    problems = lifeboat_runtime_problems()

    assert any("record_lifeboat_response_fingerprint" in problem for problem in problems)


def test_a_missing_symbol_names_its_module(monkeypatch) -> None:
    import gateway.lifeboat_modes as modes

    monkeypatch.delattr(modes, "advance_mode")

    problems = lifeboat_runtime_problems()

    assert any("gateway.lifeboat_modes" in problem for problem in problems)


def test_an_unimportable_module_is_reported(monkeypatch) -> None:
    monkeypatch.setitem(REQUIRED_SYMBOLS, "gateway.not_a_real_lifeboat_module", ("anything",))

    problems = lifeboat_runtime_problems()

    assert any("not_a_real_lifeboat_module" in problem for problem in problems)


def test_the_check_exercises_the_gate_not_just_the_imports(monkeypatch) -> None:
    """Importing cleanly is not proof the gate works; the check must run it."""
    import gateway.lifeboat_surface as surface

    monkeypatch.setattr(surface, "should_suppress_notice", lambda *a, **k: False)

    problems = lifeboat_runtime_problems()

    assert any("suppress" in problem.lower() for problem in problems)


def test_the_check_never_raises(monkeypatch) -> None:
    """A broken runtime must produce a report, not an exception at startup."""
    import gateway.lifeboat_surface as surface

    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(surface, "should_suppress_notice", explode)

    problems = lifeboat_runtime_problems()

    assert problems != ()
