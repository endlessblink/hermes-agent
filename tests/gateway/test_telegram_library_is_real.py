"""Prefer the real Telegram library over a stand-in.

The suite carries a hand-written stand-in for python-telegram-bot because the
library was not installed. Three separate failures this session came from that
stand-in disagreeing with reality: a wrong exception hierarchy that made a
timeout not count as a network error, a replacement module with no exceptions
at all, and assertions that only held because a value was an auto-generated
mock rather than a real string.

The library is a declared dependency. When it is installed the stand-ins
short-circuit and the tests run against the real thing, which is the point of
having them. This test makes which mode is in use visible instead of implicit.
"""

from __future__ import annotations

import importlib.util
import sys


def _real_library_available() -> bool:
    return importlib.util.find_spec("telegram") is not None and hasattr(
        sys.modules.get("telegram"), "__file__"
    )


def test_the_installed_library_is_used_when_present() -> None:
    """If python-telegram-bot is installed, no stand-in may shadow it."""
    if importlib.util.find_spec("telegram") is None:
        import pytest

        pytest.skip("python-telegram-bot is not installed; the stand-in is in use")

    module = sys.modules.get("telegram")
    assert module is not None
    assert hasattr(module, "__file__"), (
        "a stand-in is shadowing the installed python-telegram-bot; "
        "the mock helpers must short-circuit when the real library is present"
    )


def test_the_exception_hierarchy_is_the_real_one_when_available() -> None:
    if not _real_library_available():
        import pytest

        pytest.skip("running against the stand-in")

    from telegram.error import NetworkError, TelegramError, TimedOut

    assert issubclass(TimedOut, NetworkError)
    assert issubclass(NetworkError, TelegramError)


def test_the_stand_in_matches_the_real_hierarchy() -> None:
    """Whichever is in use, the shape tests rely on must hold."""
    from telegram.error import NetworkError, TelegramError, TimedOut

    assert issubclass(TimedOut, NetworkError)
    assert issubclass(NetworkError, TelegramError)
