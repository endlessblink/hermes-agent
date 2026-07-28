from __future__ import annotations

from datetime import datetime, timezone
from threading import Event, Thread

import pytest

from agent.personal_assistant_state import PersonalAssistantStateStore
from agent.personal_assistant_shadow_worker import (
    PERSONAL_ASSISTANT_ACCEPTANCE_PROFILE,
    PersonalAssistantShadowWorker,
    PersonalAssistantShadowWorkerLifecycle,
    build_acceptance_shadow_worker_lifecycle,
)


class RecordingAdapter:
    def __init__(self, responses: list[dict | None]) -> None:
        self.responses = list(responses)
        self.effects: list[dict] = []

    def execute(self, effect: dict) -> dict | None:
        self.effects.append(effect)
        return self.responses.pop(0) if self.responses else None


def test_shadow_worker_refuses_the_real_office_work_profile(tmp_path) -> None:
    worker = PersonalAssistantShadowWorker(
        store=PersonalAssistantStateStore(tmp_path),
        adapter=RecordingAdapter([]),
        active_profile="office-work",
    )

    assert worker.start() is False
    assert worker.running is False


def test_shadow_worker_is_single_instance_and_drains_to_idle(tmp_path) -> None:
    store = PersonalAssistantStateStore(tmp_path)
    store.apply_turn_event(
        expected_revision=0,
        event_id="submit",
        event={
            "type": "submit",
            "durableSessionId": "assistant-home",
            "submissionId": "plan-today",
            "userIntent": "תכנן את המשך היום",
        },
    )
    adapter = RecordingAdapter(
        [
            {"type": "context-evaluated", "stale": True},
            None,
        ]
    )
    worker = PersonalAssistantShadowWorker(
        store=store,
        adapter=adapter,
        active_profile=PERSONAL_ASSISTANT_ACCEPTANCE_PROFILE,
        now=lambda: datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc),
        poll_seconds=0.01,
    )

    assert worker.start() is True
    assert worker.start() is False
    assert worker.wait_until_idle(timeout=1) is True
    worker.stop(timeout=1)

    assert worker.running is False
    assert [effect["kind"] for effect in adapter.effects] == [
        "evaluate-context",
        "publish-progress-question",
    ]
    assert store.get_active_turn()["phase"] == "awaiting-context"


def test_shadow_worker_lifecycle_starts_and_stops_generated_profile() -> None:
    worker = RecordingLifecycleWorker()
    lifecycle = PersonalAssistantShadowWorkerLifecycle(
        active_profile=PERSONAL_ASSISTANT_ACCEPTANCE_PROFILE,
        build_worker=lambda: worker,
    )

    assert lifecycle.start() is True
    assert lifecycle.start() is False
    lifecycle.stop(timeout=2)

    assert worker.start_calls == 1
    assert worker.stop_timeouts == [2]


def test_shadow_worker_lifecycle_never_builds_for_office_work() -> None:
    built: list[bool] = []
    lifecycle = PersonalAssistantShadowWorkerLifecycle(
        active_profile="office-work",
        build_worker=lambda: RecordingLifecycleWorker(built),
    )

    assert lifecycle.start() is False
    lifecycle.stop(timeout=1)

    assert built == []


class RecordingLifecycleWorker:
    def __init__(self, built: list[bool] | None = None) -> None:
        if built is not None:
            built.append(True)
        self.start_calls = 0
        self.stop_timeouts: list[float] = []

    @property
    def running(self) -> bool:
        return self.start_calls > len(self.stop_timeouts)

    def start(self) -> bool:
        self.start_calls += 1
        return True

    def stop(self, *, timeout: float) -> None:
        self.stop_timeouts.append(timeout)

    def wait_until_idle(self, *, timeout: float) -> bool:
        del timeout
        return True

    def wake(self) -> None:
        pass


def test_shadow_worker_profile_gate_cannot_be_overridden(tmp_path) -> None:
    with pytest.raises(TypeError):
        PersonalAssistantShadowWorker(
            store=PersonalAssistantStateStore(tmp_path),
            adapter=RecordingAdapter([]),
            active_profile="office-work",
            allowed_profile="office-work",
        )


def test_shadow_worker_lifecycle_serializes_stop_and_replacement() -> None:
    stop_entered = Event()
    release_stop = Event()
    workers: list[BlockingStopWorker] = []

    def build_worker() -> BlockingStopWorker:
        worker = BlockingStopWorker(stop_entered, release_stop)
        workers.append(worker)
        return worker

    lifecycle = PersonalAssistantShadowWorkerLifecycle(
        active_profile=PERSONAL_ASSISTANT_ACCEPTANCE_PROFILE,
        build_worker=build_worker,
    )
    assert lifecycle.start() is True

    stop_thread = Thread(target=lifecycle.stop, kwargs={"timeout": 1})
    stop_thread.start()
    assert stop_entered.wait(1)
    restart_results: list[bool] = []
    restart_thread = Thread(target=lambda: restart_results.append(lifecycle.start()))
    restart_thread.start()

    assert restart_thread.is_alive()
    release_stop.set()
    stop_thread.join(1)
    restart_thread.join(1)

    assert restart_results == [True]
    assert len(workers) == 2


def test_shadow_worker_lifecycle_replaces_a_crashed_worker() -> None:
    workers: list[CrashedLifecycleWorker] = []

    def build_worker() -> CrashedLifecycleWorker:
        worker = CrashedLifecycleWorker(crash=len(workers) == 0)
        workers.append(worker)
        return worker

    lifecycle = PersonalAssistantShadowWorkerLifecycle(
        active_profile=PERSONAL_ASSISTANT_ACCEPTANCE_PROFILE,
        build_worker=build_worker,
    )

    assert lifecycle.start() is True
    assert lifecycle.active is False
    assert lifecycle.start() is True
    assert lifecycle.active is True
    assert len(workers) == 2


class BlockingStopWorker:
    def __init__(self, stop_entered: Event, release_stop: Event) -> None:
        self._stop_entered = stop_entered
        self._release_stop = release_stop

    @property
    def running(self) -> bool:
        return True

    def start(self) -> bool:
        return True

    def stop(self, *, timeout: float) -> None:
        self._stop_entered.set()
        if not self._release_stop.wait(timeout):
            raise TimeoutError

    def wait_until_idle(self, *, timeout: float) -> bool:
        del timeout
        return True

    def wake(self) -> None:
        pass


class CrashedLifecycleWorker:
    def __init__(self, *, crash: bool) -> None:
        self._crash = crash
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> bool:
        self._running = not self._crash
        return True

    def stop(self, *, timeout: float) -> None:
        del timeout
        self._running = False

    def wait_until_idle(self, *, timeout: float) -> bool:
        del timeout
        return True

    def wake(self) -> None:
        pass


def test_shadow_worker_reports_stop_timeout(tmp_path) -> None:
    store = PersonalAssistantStateStore(tmp_path)
    store.apply_turn_event(
        expected_revision=0,
        event_id="submit",
        event={
            "type": "submit",
            "durableSessionId": "assistant-home",
            "submissionId": "plan-today",
            "userIntent": "תכנן את המשך היום",
        },
    )
    adapter = BlockingAdapter()
    worker = PersonalAssistantShadowWorker(
        store=store,
        adapter=adapter,
        active_profile=PERSONAL_ASSISTANT_ACCEPTANCE_PROFILE,
    )
    assert worker.start() is True
    assert adapter.entered.wait(1)

    with pytest.raises(TimeoutError, match="did not stop"):
        worker.stop(timeout=0.01)

    adapter.release.set()
    worker.stop(timeout=1)


class BlockingAdapter:
    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()

    def execute(self, effect: dict) -> None:
        del effect
        self.entered.set()
        self.release.wait(1)


def test_bound_acceptance_lifecycle_drains_a_generated_plan(tmp_path) -> None:
    store = PersonalAssistantStateStore(tmp_path)
    store.get_planning_interview = lambda: {"readinessApproved": True}
    store.apply_turn_event(
        expected_revision=0,
        event_id="submit",
        event={
            "type": "submit",
            "durableSessionId": "assistant-home",
            "submissionId": "plan-today",
            "userIntent": "תכנן את המשך היום",
        },
    )
    lifecycle = build_acceptance_shadow_worker_lifecycle(
        store=store,
        active_profile=PERSONAL_ASSISTANT_ACCEPTANCE_PROFILE,
        resolve_agent=lambda _session_id: object(),
        recover_runtime=lambda _session_id, _rejected: "runtime-1",
        planning_response_builder=lambda *_args: "validated plan",
        extract_recommendations=lambda _response: [
            {"taskId": "task-1", "title": "משימה לדוגמה"}
        ],
        needs_progress_check=lambda _interview, *, now: False,
        poll_seconds=0.01,
    )

    assert lifecycle.start() is True
    assert lifecycle.wait_until_idle(timeout=1) is True
    lifecycle.stop(timeout=1)

    active_turn = store.get_active_turn()
    assert active_turn["phase"] == "completed"
    assert active_turn["visibleOutcome"]["kind"] == "plan"
    assert active_turn["visibleOutcome"]["recommendations"] == [
        {"taskId": "task-1", "title": "משימה לדוגמה"}
    ]
