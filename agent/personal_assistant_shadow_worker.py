"""Profile-gated background worker for Personal Assistant shadow turns."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from threading import Event, Lock, Thread
from typing import Any, Protocol

from agent.personal_assistant_state import PersonalAssistantStateStore
from agent.personal_assistant_turn_engine import (
    PersonalAssistantTurnAdapter,
    drain_one_turn_effect,
)

PERSONAL_ASSISTANT_ACCEPTANCE_PROFILE = "personal-assistant-acceptance"


class ShadowWorker(Protocol):
    @property
    def running(self) -> bool: ...

    def start(self) -> bool: ...

    def stop(self, *, timeout: float) -> None: ...

    def wait_until_idle(self, *, timeout: float) -> bool: ...

    def wake(self) -> None: ...


class PersonalAssistantShadowWorkerLifecycle:
    """Own one generated-profile worker for a surrounding process lifecycle."""

    def __init__(
        self,
        *,
        active_profile: str,
        build_worker: Callable[[], ShadowWorker],
    ) -> None:
        self._active_profile = active_profile
        self._build_worker = build_worker
        self._lock = Lock()
        self._worker: ShadowWorker | None = None

    @property
    def active(self) -> bool:
        with self._lock:
            return self._worker is not None and self._worker.running

    def start(self) -> bool:
        if self._active_profile != PERSONAL_ASSISTANT_ACCEPTANCE_PROFILE:
            return False
        with self._lock:
            if self._worker is not None:
                if self._worker.running:
                    return False
                self._worker = None
            worker = self._build_worker()
            if not worker.start():
                return False
            self._worker = worker
            return True

    def stop(self, *, timeout: float) -> None:
        with self._lock:
            worker = self._worker
            if worker is None:
                return
            worker.stop(timeout=timeout)
            self._worker = None

    def wait_until_idle(self, *, timeout: float) -> bool:
        with self._lock:
            worker = self._worker
        return worker is not None and worker.wait_until_idle(timeout=timeout)

    def wake(self) -> None:
        with self._lock:
            worker = self._worker
        if worker is not None:
            worker.wake()


def build_acceptance_shadow_worker_lifecycle(
    *,
    store: PersonalAssistantStateStore,
    active_profile: str,
    resolve_agent: Callable[[str], Any | None],
    recover_runtime: Callable[[str, tuple[str, ...]], str | None],
    planning_response_builder: Callable[..., str | None] | None = None,
    extract_recommendations: Callable[[Any], list[dict[str, str]]] | None = None,
    needs_progress_check: Callable[..., bool] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    poll_seconds: float = 0.25,
) -> PersonalAssistantShadowWorkerLifecycle:
    from agent.personal_assistant_production_adapters import (
        _default_extract_recommendations,
        _default_needs_progress_check,
        build_shadow_effect_router,
    )

    adapter = build_shadow_effect_router(
        store=store,
        resolve_agent=resolve_agent,
        recover_runtime=recover_runtime,
        planning_response_builder=planning_response_builder,
        extract_recommendations=(
            extract_recommendations or _default_extract_recommendations
        ),
        needs_progress_check=needs_progress_check or _default_needs_progress_check,
        now=now,
    )
    return PersonalAssistantShadowWorkerLifecycle(
        active_profile=active_profile,
        build_worker=lambda: PersonalAssistantShadowWorker(
            store=store,
            adapter=adapter,
            active_profile=active_profile,
            now=now,
            poll_seconds=poll_seconds,
        ),
    )


class PersonalAssistantShadowWorker:
    def __init__(
        self,
        *,
        store: PersonalAssistantStateStore,
        adapter: PersonalAssistantTurnAdapter,
        active_profile: str,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        poll_seconds: float = 0.25,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("shadow worker poll interval must be positive")
        self._store = store
        self._adapter = adapter
        self._active_profile = active_profile
        self._now = now
        self._poll_seconds = poll_seconds
        self._stop = Event()
        self._wake = Event()
        self._idle = Event()
        self._lock = Lock()
        self._thread: Thread | None = None
        self._last_error: Exception | None = None

    @property
    def last_error(self) -> Exception | None:
        return self._last_error

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> bool:
        if self._active_profile != PERSONAL_ASSISTANT_ACCEPTANCE_PROFILE:
            return False
        with self._lock:
            if self.running:
                return False
            self._last_error = None
            self._stop.clear()
            self._wake.set()
            self._idle.clear()
            self._thread = Thread(
                target=self._run,
                name="personal-assistant-shadow-worker",
                daemon=True,
            )
            self._thread.start()
            return True

    def wake(self) -> None:
        self._idle.clear()
        self._wake.set()

    def wait_until_idle(self, *, timeout: float) -> bool:
        return self._idle.wait(timeout)

    def stop(self, *, timeout: float) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                raise TimeoutError("shadow worker did not stop before timeout")

    def _run(self) -> None:
        worker_id = f"pa-shadow:{self._active_profile}"
        while not self._stop.is_set():
            try:
                delivered = drain_one_turn_effect(
                    self._store,
                    self._adapter,
                    worker_id=worker_id,
                    now=self._now(),
                )
            except Exception as exc:
                self._last_error = exc
                self._idle.set()
                self._stop.set()
                return
            if delivered is not None:
                continue
            self._idle.set()
            self._wake.wait(self._poll_seconds)
            self._wake.clear()
