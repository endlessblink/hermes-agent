"""Shared cron-store mutation boundary for external scheduler synchronization.

``cron.jobs`` intentionally stays independent of scheduler providers.  User
surfaces that mutate the store must cross this small adapter so an external
provider can reconcile its remote schedule after the local write succeeds.
The built-in provider's notification is a no-op.
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar


T = TypeVar("T")


def notify_provider_jobs_changed() -> None:
    """Best-effort notification to the active scheduler provider."""

    from cron.scheduler import _notify_provider_jobs_changed

    _notify_provider_jobs_changed()


def mutate_job_store(mutation: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run one store mutation and notify only when it reports success."""

    result = mutation(*args, **kwargs)
    if result:
        notify_provider_jobs_changed()
    return result


__all__ = ["mutate_job_store", "notify_provider_jobs_changed"]
