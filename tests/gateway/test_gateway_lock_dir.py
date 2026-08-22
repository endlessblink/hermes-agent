"""The gateway lock directory must always be an absolute path.

An empty environment variable is not the same as an unset one: os.getenv
returns the empty string and the default never applies. That turned the lock
directory into a relative path, so gateway locks would be created wherever the
process happened to be started — and in a checkout containing a file named
"hermes", creating it failed outright.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway.status import _get_lock_dir


def test_the_lock_dir_is_absolute_by_default(monkeypatch) -> None:
    monkeypatch.delenv("HERMES_GATEWAY_LOCK_DIR", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)

    assert _get_lock_dir().is_absolute()


def test_an_empty_state_home_is_treated_as_unset(monkeypatch) -> None:
    monkeypatch.delenv("HERMES_GATEWAY_LOCK_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", "")

    assert _get_lock_dir().is_absolute()


def test_a_whitespace_state_home_is_treated_as_unset(monkeypatch) -> None:
    monkeypatch.delenv("HERMES_GATEWAY_LOCK_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", "   ")

    assert _get_lock_dir().is_absolute()


def test_an_empty_override_is_treated_as_unset(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_GATEWAY_LOCK_DIR", "")
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)

    assert _get_lock_dir().is_absolute()


def test_a_real_state_home_is_honoured(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("HERMES_GATEWAY_LOCK_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert _get_lock_dir() == tmp_path / "hermes" / "gateway-locks"


def test_a_real_override_wins(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_GATEWAY_LOCK_DIR", str(tmp_path / "locks"))

    assert _get_lock_dir() == tmp_path / "locks"


def test_a_relative_override_is_made_absolute(monkeypatch) -> None:
    """A relative lock dir would follow the working directory around."""
    monkeypatch.setenv("HERMES_GATEWAY_LOCK_DIR", "hermes/gateway-locks")

    assert _get_lock_dir().is_absolute()
