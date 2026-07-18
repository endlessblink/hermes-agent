import asyncio
import threading
from types import SimpleNamespace

from gateway.run import _start_secondary_profile_cron_schedulers
from hermes_constants import get_hermes_home


def test_secondary_profile_cron_runs_in_profile_scope(monkeypatch, tmp_path):
    office_home = tmp_path / "profiles" / "office-work"
    office_home.mkdir(parents=True)
    started = threading.Event()
    stop = threading.Event()
    seen = {}

    class Provider:
        name = "fake"

        def start(self, stop_event, **kwargs):
            seen["home"] = get_hermes_home()
            seen["adapters"] = kwargs["adapters"]
            started.set()
            stop_event.wait(1)

        def stop(self):
            pass

    runner = SimpleNamespace(
        config=SimpleNamespace(
            multiplex_profiles=True,
            multiplex_served_profiles=["office-work"],
        ),
        adapters={"telegram": "default-adapter"},
        _profile_adapters={"office-work": {"telegram": "office-adapter"}},
        _draining=False,
        _external_drain_active=False,
    )
    monkeypatch.setattr(
        "hermes_cli.profiles.profiles_to_serve",
        lambda **_kwargs: [("default", tmp_path), ("office-work", office_home)],
    )
    monkeypatch.setattr(
        "hermes_cli.profiles.get_active_profile_name", lambda: "default"
    )

    schedulers = _start_secondary_profile_cron_schedulers(
        runner,
        stop,
        asyncio.new_event_loop(),
        resolve_provider=lambda: Provider(),
    )
    try:
        assert started.wait(1)
        assert len(schedulers) == 1
        assert seen == {
            "home": office_home,
            "adapters": {"telegram": "office-adapter"},
        }
    finally:
        stop.set()
        for _provider, thread in schedulers:
            thread.join(timeout=1)


def test_secondary_profile_cron_is_disabled_without_multiplexing():
    runner = SimpleNamespace(
        config=SimpleNamespace(multiplex_profiles=False),
    )

    assert _start_secondary_profile_cron_schedulers(
        runner,
        threading.Event(),
        asyncio.new_event_loop(),
    ) == []


def test_secondary_profile_cron_does_not_activate_unrelated_profiles(
    monkeypatch, tmp_path
):
    runner = SimpleNamespace(
        config=SimpleNamespace(
            multiplex_profiles=True,
            multiplex_served_profiles=["life-advisor"],
        ),
    )
    monkeypatch.setattr(
        "hermes_cli.profiles.profiles_to_serve",
        lambda **_kwargs: [
            ("default", tmp_path),
            ("life-advisor", tmp_path / "profiles" / "life-advisor"),
        ],
    )
    monkeypatch.setattr(
        "hermes_cli.profiles.get_active_profile_name", lambda: "default"
    )

    assert _start_secondary_profile_cron_schedulers(
        runner,
        threading.Event(),
        asyncio.new_event_loop(),
    ) == []
