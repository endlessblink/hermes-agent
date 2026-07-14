"""Single-poller Telegram routing across multiplexed profiles."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.run import GatewayRunner
from gateway.session import SessionSource


ROUTES = {
    "telegram": {
        "topics": {"-1004230590253": {"2": "life-advisor"}},
        "chats": {"602196268": "life-advisor"},
        "default": "default",
    }
}


def _source(chat_id: str, thread_id: str | None = None, profile: str | None = None):
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id=chat_id,
        chat_type="forum" if thread_id else "dm",
        user_id="602196268",
        thread_id=thread_id,
        profile=profile,
    )


def _runner() -> GatewayRunner:
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        multiplex_profiles=True,
        profile_routes=deepcopy(ROUTES),
    )
    runner.adapters = {Platform.TELEGRAM: object()}
    runner._profile_adapters = {"life-advisor": {}}
    return runner


def test_profile_routes_round_trip_and_accept_nested_gateway_form():
    config = GatewayConfig.from_dict(
        {
            "gateway": {
                "multiplex_profiles": True,
                "profile_routes": ROUTES,
            }
        }
    )

    assert config.profile_routes == ROUTES
    assert GatewayConfig.from_dict(config.to_dict()).profile_routes == ROUTES


def test_topic_route_beats_chat_route_then_falls_back_to_default():
    runner = _runner()
    runner.config.profile_routes["telegram"]["chats"]["-1004230590253"] = "default"

    assert runner._resolve_inbound_profile(_source("-1004230590253", "2")) == "life-advisor"
    assert runner._resolve_inbound_profile(_source("-1004230590253", "9")) == "default"
    assert runner._resolve_inbound_profile(_source("999")) == "default"


def test_yaml_numeric_chat_and_topic_keys_are_normalized():
    runner = _runner()
    runner.config.profile_routes = {
        "telegram": {
            "topics": {-1004230590253: {2: "life-advisor"}},
            "chats": {602196268: "life-advisor"},
            "default": "default",
        }
    }

    assert runner._resolve_inbound_profile(_source("-1004230590253", "2")) == "life-advisor"
    assert runner._resolve_inbound_profile(_source("602196268")) == "life-advisor"


def test_route_table_is_authoritative_over_untrusted_existing_stamp():
    runner = _runner()

    assert runner._resolve_inbound_profile(_source("999", profile="life-advisor")) == "default"


def test_existing_profile_stamp_is_preserved_without_route_table():
    runner = _runner()
    runner.config.profile_routes = {}

    assert runner._resolve_inbound_profile(_source("999", profile="life-advisor")) == "life-advisor"


@pytest.mark.asyncio
async def test_route_is_stamped_before_dispatch_and_runs_in_target_scope(monkeypatch):
    runner = _runner()
    seen = {}

    async def handle(event):
        seen["profile"] = event.source.profile
        seen["scope"] = active_scope[0]
        return "ok"

    active_scope = []

    class _Scope:
        def __enter__(self):
            active_scope.append("life-advisor")

        def __exit__(self, *_args):
            active_scope.pop()

    runner._handle_message = handle
    runner._resolve_profile_home_for_source = lambda source: Path("/profiles") / source.profile
    monkeypatch.setattr("gateway.run._profile_runtime_scope", lambda _home: _Scope())
    event = SimpleNamespace(source=_source("602196268"))

    assert await runner._handle_routed_message(event) == "ok"
    assert seen == {"profile": "life-advisor", "scope": "life-advisor"}


@pytest.mark.asyncio
async def test_unknown_profile_fails_closed_before_dispatch():
    runner = _runner()
    runner.config.profile_routes["telegram"]["default"] = "ghost"
    runner._handle_message = AsyncMock()

    assert await runner._handle_routed_message(SimpleNamespace(source=_source("999"))) is None
    runner._handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_token_route_binds_one_physical_adapter(monkeypatch):
    owner_adapter = SimpleNamespace(token="same-token")
    duplicate = SimpleNamespace(
        token="same-token",
        connect=AsyncMock(side_effect=AssertionError("must not poll twice")),
        disconnect=AsyncMock(),
    )
    runner = _runner()
    runner.adapters = {Platform.TELEGRAM: owner_adapter}
    runner._profile_adapters = {}
    profile_cfg = GatewayConfig(
        multiplex_profiles=True,
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="same-token")},
    )
    monkeypatch.setattr("gateway.config.load_gateway_config", lambda: profile_cfg)
    monkeypatch.setattr(runner, "_create_adapter", lambda _platform, _config: duplicate)
    monkeypatch.setattr(runner, "_safe_adapter_disconnect", AsyncMock())
    claimed = {
        (Platform.TELEGRAM, runner._adapter_credential_fingerprint(owner_adapter)): "default"
    }

    connected = await runner._start_one_profile_adapters(
        "life-advisor", Path("/profiles/life-advisor"), claimed
    )

    assert connected == 0
    assert runner._profile_adapters["life-advisor"][Platform.TELEGRAM] is owner_adapter
    duplicate.connect.assert_not_awaited()
    runner._safe_adapter_disconnect.assert_awaited_once_with(duplicate, Platform.TELEGRAM)


def test_primary_adapter_is_bound_to_routed_profile_before_polling(monkeypatch):
    runner = _runner()
    runner._profile_adapters = {}
    runner.pairing_stores = {}
    adapter = object()
    store = object()
    monkeypatch.setattr(
        "hermes_cli.profiles.profiles_to_serve",
        lambda multiplex, served_profiles=None: [
            ("default", Path("/profiles/default")),
            ("life-advisor", Path("/profiles/life-advisor")),
        ],
    )
    monkeypatch.setattr("gateway.pairing.PairingStore", lambda profile: store)

    runner._bind_primary_shared_adapter_routes(Platform.TELEGRAM, adapter)

    assert runner._profile_adapters["life-advisor"][Platform.TELEGRAM] is adapter
    assert runner.pairing_stores["life-advisor"] is store


@pytest.mark.asyncio
async def test_independent_secondary_handler_enters_profile_scope(tmp_path, monkeypatch):
    runner = _runner()
    life_home = tmp_path / "life-advisor"
    life_home.mkdir()
    (life_home / ".env").write_text(
        "TELEGRAM_ALLOWED_USERS=life-user\n", encoding="utf-8"
    )
    runner._resolve_profile_home_for_source = lambda _source: life_home
    runner.pairing_store = MagicMock()
    runner.pairing_stores = {"life-advisor": MagicMock()}
    runner.pairing_stores["life-advisor"].is_approved.return_value = False
    seen = {}

    async def handle(event):
        seen["authorized"] = runner._is_user_authorized(event.source)
        return "ok"

    runner._handle_message = handle
    event = SimpleNamespace(source=_source("unrouted", profile=None))
    event.source.user_id = "life-user"
    handler = runner._make_profile_message_handler("life-advisor")

    assert await handler(event) == "ok"
    assert seen == {"authorized": True}


def test_shared_adapter_uses_target_profile_pairing_store():
    runner = _runner()
    default_store = MagicMock()
    life_store = MagicMock()
    runner.pairing_store = default_store
    runner.pairing_stores = {"life-advisor": life_store}
    runner._profile_adapters["life-advisor"][Platform.TELEGRAM] = runner.adapters[
        Platform.TELEGRAM
    ]
    source = _source("602196268")
    source.profile = runner._resolve_inbound_profile(source)

    assert runner._pairing_store_for(source) is life_store
    assert runner._adapter_for_source(source) is runner.adapters[Platform.TELEGRAM]


def test_shared_route_authorizes_from_target_profile_env(tmp_path, monkeypatch):
    runner = _runner()
    owner_adapter = runner.adapters[Platform.TELEGRAM]
    runner._profile_adapters["life-advisor"][Platform.TELEGRAM] = owner_adapter
    runner.pairing_store = MagicMock()
    runner.pairing_stores = {"life-advisor": MagicMock()}
    runner.pairing_stores["life-advisor"].is_approved.return_value = False
    life_home = tmp_path / "life-advisor"
    life_home.mkdir()
    (life_home / ".env").write_text(
        "TELEGRAM_ALLOWED_USERS=life-user\n", encoding="utf-8"
    )
    runner._resolve_profile_home_for_source = lambda _source: life_home
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "default-user")
    source = _source("602196268")
    source.user_id = "life-user"

    assert runner._is_user_authorized_for_inbound_source(source) is True
    source.user_id = "default-user"
    assert runner._is_user_authorized_for_inbound_source(source) is False


@pytest.mark.asyncio
async def test_unauthorized_routed_dm_generates_pairing_in_target_store(tmp_path, monkeypatch):
    runner = _runner()
    owner_adapter = SimpleNamespace(send=AsyncMock())
    runner.adapters = {Platform.TELEGRAM: owner_adapter}
    runner._profile_adapters["life-advisor"][Platform.TELEGRAM] = owner_adapter
    runner.pairing_store = MagicMock()
    life_store = MagicMock()
    life_store.is_approved.return_value = False
    life_store._is_rate_limited.return_value = False
    life_store.generate_code.return_value = "LIFE1234"
    runner.pairing_stores = {"life-advisor": life_store}
    runner.session_store = MagicMock()
    runner._running_agents = {}
    runner._update_prompt_pending = {}
    life_home = tmp_path / "life-advisor"
    life_home.mkdir()
    (life_home / ".env").write_text("", encoding="utf-8")
    runner._resolve_profile_home_for_source = lambda _source: life_home
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_args, **_kwargs: [])
    event = MessageEvent(
        text="hello",
        message_id="m1",
        source=_source("602196268"),
    )

    assert await runner._handle_routed_message(event) is None
    life_store.generate_code.assert_called_once_with(
        "telegram", "602196268", ""
    )
    runner.pairing_store.generate_code.assert_not_called()
    owner_adapter.send.assert_awaited_once()
