"""The fake telegram exception hierarchy must match the real library.

Fourteen test files each built their own, and made TimedOut a subclass of
OSError instead of NetworkError -- so a timeout was not recognised as a network
error and reconnect logic was tested against a lie.
"""

from __future__ import annotations

import pytest

from tests.gateway.telegram_mock import telegram_error_module


def test_the_hierarchy_matches_python_telegram_bot() -> None:
    errors = telegram_error_module()

    assert issubclass(errors.TimedOut, errors.NetworkError)
    assert issubclass(errors.BadRequest, errors.NetworkError)
    assert issubclass(errors.NetworkError, errors.TelegramError)


def test_semantic_errors_are_not_network_errors() -> None:
    errors = telegram_error_module()

    assert not issubclass(errors.Forbidden, errors.NetworkError)
    assert not issubclass(errors.InvalidToken, errors.NetworkError)
    assert not issubclass(errors.RetryAfter, errors.NetworkError)


def test_the_module_is_a_singleton() -> None:
    """Identity is the whole point: captured classes must stay the same object."""
    assert telegram_error_module() is telegram_error_module()
    assert telegram_error_module().TimedOut is telegram_error_module().TimedOut


def test_retry_after_keeps_its_payload() -> None:
    errors = telegram_error_module()

    assert errors.RetryAfter(7).retry_after == 7


@pytest.mark.parametrize("name", ["NetworkError", "TimedOut"])
def test_the_adapter_recognises_network_errors(name: str) -> None:
    from plugins.platforms.telegram.adapter import TelegramAdapter

    error_type = getattr(telegram_error_module(), name)

    assert TelegramAdapter._looks_like_network_error(error_type(name)) is True


@pytest.mark.parametrize("name", ["Forbidden", "InvalidToken"])
def test_the_adapter_rejects_semantic_errors(name: str) -> None:
    from plugins.platforms.telegram.adapter import TelegramAdapter

    error_type = getattr(telegram_error_module(), name)

    assert TelegramAdapter._looks_like_network_error(error_type(name)) is False
