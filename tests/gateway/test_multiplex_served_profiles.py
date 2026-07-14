"""Opt-in profile selection for a multiplexed gateway."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from gateway.config import GatewayConfig


@pytest.fixture()
def profile_env(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    default_home = tmp_path / ".hermes"
    default_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(default_home))
    return tmp_path


def test_served_profile_allowlist_defaults_to_legacy_all_profiles():
    config = GatewayConfig()

    assert config.multiplex_served_profiles is None
    assert GatewayConfig.from_dict(config.to_dict()).multiplex_served_profiles is None


def test_served_profile_allowlist_accepts_nested_gateway_config():
    config = GatewayConfig.from_dict(
        {
            "gateway": {
                "multiplex_profiles": True,
                "multiplex_served_profiles": ["life-advisor"],
            }
        }
    )

    assert config.multiplex_served_profiles == ["life-advisor"]
    assert GatewayConfig.from_dict(config.to_dict()).multiplex_served_profiles == [
        "life-advisor"
    ]


def test_profiles_to_serve_filters_named_profiles_but_always_keeps_default(
    profile_env,
):
    from hermes_cli.profiles import create_profile, profiles_to_serve

    create_profile("content-creator", no_alias=True)
    create_profile("life-advisor", no_alias=True)

    assert [
        name
        for name, _home in profiles_to_serve(
            multiplex=True,
            served_profiles=["life-advisor"],
        )
    ] == ["default", "life-advisor"]
    assert [
        name
        for name, _home in profiles_to_serve(
            multiplex=True,
            served_profiles=[],
        )
    ] == ["default"]


def test_profiles_to_serve_legacy_absent_allowlist_still_returns_all(profile_env):
    from hermes_cli.profiles import create_profile, profiles_to_serve

    create_profile("content-creator", no_alias=True)
    create_profile("life-advisor", no_alias=True)

    assert {
        name for name, _home in profiles_to_serve(multiplex=True)
    } == {"default", "content-creator", "life-advisor"}


def test_profiles_to_serve_excludes_unknown_names_safely(profile_env):
    from hermes_cli.profiles import profiles_to_serve

    assert [
        name
        for name, _home in profiles_to_serve(
            multiplex=True,
            served_profiles=["ghost"],
        )
    ] == ["default"]


@pytest.mark.asyncio
async def test_gateway_starts_only_configured_secondary_profile(monkeypatch):
    from gateway.run import GatewayRunner

    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        multiplex_profiles=True,
        multiplex_served_profiles=["life-advisor"],
    )
    runner.adapters = {}
    runner._profile_adapters = {}
    runner.pairing_stores = {}
    runner._start_one_profile_adapters = AsyncMock(return_value=1)
    monkeypatch.setattr("hermes_cli.profiles.get_active_profile_name", lambda: "default")
    monkeypatch.setattr(
        "hermes_cli.profiles.profiles_to_serve",
        lambda multiplex, served_profiles=None: [
            ("default", "/profiles/default"),
            ("life-advisor", "/profiles/life-advisor"),
        ]
        if served_profiles == ["life-advisor"]
        else pytest.fail("gateway did not pass the configured served-profile allowlist"),
    )
    monkeypatch.setattr("gateway.status.write_runtime_status", lambda **_kwargs: None)

    assert await runner._start_secondary_profile_adapters() == 1
    runner._start_one_profile_adapters.assert_awaited_once()
    assert runner._start_one_profile_adapters.await_args.args[:2] == (
        "life-advisor",
        "/profiles/life-advisor",
    )
