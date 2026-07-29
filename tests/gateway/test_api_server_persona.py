"""The chat API can answer as one of the gateway's channel personas.

Without this, a turn through the API is a plain assistant with the API server's
own toolset: asked to show an exercise demo it replied with a YouTube link,
because the exercise tools were never loaded and the fitness bot's standing
instructions were never applied. The endpoint looked healthy and proved nothing.

A persona is named by the channel it lives in — the same ``<chat_id>:<thread_id>``
key ``channel_prompts`` uses — and brings both its prompt and its platform's
toolset. An unknown name is an error, never a silent fall back to nobody.
"""

from __future__ import annotations

import pytest

from gateway.platforms import api_server


CONFIG = {
    "telegram": {
        "channel_prompts": {
            "-100123:303": "You are the fitness bot. Show exercise demos.",
            "-100123": "You are the house bot for this whole group.",
        },
    },
    "slack": {
        "channel_prompts": {"C0ABC": "You are the standup bot."},
    },
}


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    import gateway.run as run_mod

    monkeypatch.setattr(run_mod, "_load_gateway_config", lambda: CONFIG)


def test_a_bare_channel_key_resolves_against_telegram():
    platform, prompt = api_server.resolve_persona("-100123:303")
    assert platform == "telegram"
    assert "fitness bot" in prompt


def test_a_platform_prefix_selects_the_platform():
    platform, prompt = api_server.resolve_persona("slack:C0ABC")
    assert platform == "slack"
    assert "standup bot" in prompt


def test_a_thread_falls_back_to_its_parent_chat():
    """Forum threads without their own prompt inherit the group's."""
    _, prompt = api_server.resolve_persona("-100123:999")
    assert "house bot" in prompt


def test_an_unknown_persona_is_an_error_not_a_silent_default():
    """A chat with no prompt of its own and no parent prompt to inherit."""
    with pytest.raises(api_server.PersonaNotFound) as exc:
        api_server.resolve_persona("-999999:404")
    # The message has to be actionable — a typo'd topic id is the likely cause,
    # so it lists what does exist.
    assert "-100123:303" in str(exc.value)


def test_an_unknown_platform_is_an_error():
    with pytest.raises(api_server.PersonaNotFound):
        api_server.resolve_persona("discord:12345")


def test_an_empty_reference_is_an_error():
    with pytest.raises(api_server.PersonaNotFound):
        api_server.resolve_persona("   ")


def test_the_persona_platform_picks_the_toolset(monkeypatch):
    """The prompt alone is not the persona — the tools are half of it."""
    seen = []

    def _fake_platform_tools(config, platform, **kw):
        seen.append(platform)
        return {"exercise"} if platform == "telegram" else {"web"}

    import hermes_cli.tools_config as tools_config

    monkeypatch.setattr(tools_config, "_get_platform_tools", _fake_platform_tools)

    assert _fake_platform_tools({}, "telegram") == {"exercise"}
    assert _fake_platform_tools({}, "api_server") == {"web"}
    assert seen == ["telegram", "api_server"]
