"""One fake telegram exception hierarchy, shared by every test that needs it.

The real library is not installed, so the suite runs against mocks. Fourteen
test files each built their own exception classes, and they disagreed with the
real library and with each other: TimedOut was made a subclass of OSError
rather than of NetworkError, so code asking "is this a network error?" answered
no for a timeout.

That produced failures that looked like flaky ordering -- a module captures
NetworkError at import time, a later file rebuilds it, and isinstance stops
matching -- while the underlying problem was simply that the mock modelled the
wrong hierarchy.

Built once, here, matching python-telegram-bot 22.x:

    TelegramError
    └── NetworkError
        ├── TimedOut
        └── BadRequest
"""

from __future__ import annotations

import sys
from types import ModuleType


_MODULE_NAME = "telegram.error"


def _build() -> ModuleType:
    module = ModuleType(_MODULE_NAME)

    module.TelegramError = type("TelegramError", (Exception,), {})
    module.NetworkError = type("NetworkError", (module.TelegramError,), {})
    module.TimedOut = type("TimedOut", (module.NetworkError,), {})
    module.BadRequest = type("BadRequest", (module.NetworkError,), {})
    module.Forbidden = type("Forbidden", (module.TelegramError,), {})
    module.InvalidToken = type("InvalidToken", (module.TelegramError,), {})
    module.Conflict = type("Conflict", (module.TelegramError,), {})
    module.ChatMigrated = type("ChatMigrated", (module.TelegramError,), {})

    class RetryAfter(module.TelegramError):
        def __init__(self, retry_after=1):
            super().__init__(f"retry after {retry_after}")
            self.retry_after = retry_after

    module.RetryAfter = RetryAfter
    return module


def telegram_error_module() -> ModuleType:
    """Return the one shared fake ``telegram.error``, creating it if needed.

    Registered in ``sys.modules`` so that a module which did ``from
    telegram.error import NetworkError`` at import time holds the same class
    object every other test sees.
    """
    existing = sys.modules.get(_MODULE_NAME)
    if existing is not None and hasattr(existing, "TimedOut"):
        return existing
    module = _build()
    sys.modules[_MODULE_NAME] = module
    return module


def attach_telegram_errors(mock_module) -> None:
    """Give a test's own telegram mock the shared exception hierarchy."""
    mock_module.error = telegram_error_module()
