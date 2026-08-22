"""A queued turn must survive a restart.

Since 2026-08-20 the Life-Boat session could not auto-resume: the authorization
check that guards a resume reads a platform credential, and while multiplexing
is on that read must happen inside the profile's secret scope or it refuses,
rather than risk handing back another profile's value.

The check ran outside that scope, so it raised, the resume was skipped, and a
message the user had already sent simply sat there. It failed silently as a
warning in a log nobody reads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway.run import GatewayRunner


def test_the_runner_exposes_a_scoped_authorization_check() -> None:
    """The fix is a named seam, so it cannot quietly regress to an unscoped call."""
    assert hasattr(GatewayRunner, "_is_user_authorized_in_profile_scope")


def test_the_scoped_check_is_used_by_startup_resume() -> None:
    import inspect

    source = inspect.getsource(GatewayRunner._restore_pending_sessions) \
        if hasattr(GatewayRunner, "_restore_pending_sessions") else ""
    if not source:
        source = Path(GatewayRunner.__module__.replace(".", "/") + ".py").read_text(
            encoding="utf-8", errors="replace"
        )

    assert "_is_user_authorized_in_profile_scope" in source


def test_the_scoped_check_falls_back_when_no_profile_home_is_known(monkeypatch) -> None:
    """No profile to scope to is not a reason to refuse a resume."""
    runner = object.__new__(GatewayRunner)
    calls = []

    monkeypatch.setattr(
        GatewayRunner, "_is_user_authorized", lambda self, source: calls.append(source) or True
    )
    monkeypatch.setattr(
        GatewayRunner, "_resolve_profile_home_for_source", lambda self, source: None
    )

    assert runner._is_user_authorized_in_profile_scope(object()) is True
    assert calls


def test_the_scoped_check_enters_the_profile_scope_when_one_exists(monkeypatch, tmp_path) -> None:
    runner = object.__new__(GatewayRunner)
    entered = []

    import gateway.run as gateway_run

    class _Scope:
        def __enter__(self):
            entered.append("in")
            return self

        def __exit__(self, *exc):
            entered.append("out")
            return False

    monkeypatch.setattr(gateway_run, "_profile_runtime_scope", lambda home: _Scope())
    monkeypatch.setattr(
        GatewayRunner, "_resolve_profile_home_for_source", lambda self, source: tmp_path
    )
    monkeypatch.setattr(GatewayRunner, "_is_user_authorized", lambda self, source: True)

    assert runner._is_user_authorized_in_profile_scope(object()) is True
    assert entered == ["in", "out"]


def test_an_unauthorized_owner_is_still_refused(monkeypatch, tmp_path) -> None:
    """Scoping the read must not weaken the guard it exists to serve."""
    runner = object.__new__(GatewayRunner)

    import gateway.run as gateway_run

    class _Scope:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(gateway_run, "_profile_runtime_scope", lambda home: _Scope())
    monkeypatch.setattr(
        GatewayRunner, "_resolve_profile_home_for_source", lambda self, source: tmp_path
    )
    monkeypatch.setattr(GatewayRunner, "_is_user_authorized", lambda self, source: False)

    assert runner._is_user_authorized_in_profile_scope(object()) is False
