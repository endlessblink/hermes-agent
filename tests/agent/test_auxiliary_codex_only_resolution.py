"""Regression tests for codex-only auxiliary resolution (2026-07-15).

A codex-only setup (ChatGPT OAuth, no OpenRouter/Nous/API keys) used to
strand the auxiliary router two independent ways:

1. Scalar ``model: <slug>`` configs name no provider, so
   ``_read_main_provider()`` returned "" and Step 1 (main provider) of the
   auto-detect chain never ran.
2. ``_try_main_fallback_chain`` unconditionally skipped every fallback
   entry whose provider matched the main provider — in an all-codex config
   (main openai-codex, fallbacks openai-codex gpt-5.4/5.5) that skipped the
   entire chain even when Step 1 never actually ran.

Both left "no provider available" and the summarizer dead.
"""

from unittest.mock import MagicMock, patch

from agent.auxiliary_client import (
    _mark_provider_unhealthy,
    _read_main_provider,
    _try_main_fallback_chain,
)


class TestScalarModelProviderFallback:
    def test_scalar_model_config_uses_active_auth_provider(self, monkeypatch):
        """model: gpt-5.6-sol (scalar) → provider comes from auth store."""
        monkeypatch.setattr("agent.auxiliary_client._RUNTIME_MAIN_PROVIDER", "")
        with patch("hermes_cli.config.load_config",
                   return_value={"model": "gpt-5.6-sol"}), \
             patch("hermes_cli.auth.get_active_provider",
                   return_value="openai-codex"):
            assert _read_main_provider() == "openai-codex"

    def test_dict_config_with_provider_still_wins(self, monkeypatch):
        monkeypatch.setattr("agent.auxiliary_client._RUNTIME_MAIN_PROVIDER", "")
        with patch("hermes_cli.config.load_config",
                   return_value={"model": {"default": "m", "provider": "Alibaba"}}), \
             patch("hermes_cli.auth.get_active_provider",
                   return_value="openai-codex"):
            assert _read_main_provider() == "alibaba"

    def test_no_config_no_auth_returns_empty(self, monkeypatch):
        monkeypatch.setattr("agent.auxiliary_client._RUNTIME_MAIN_PROVIDER", "")
        with patch("hermes_cli.config.load_config", return_value={}), \
             patch("hermes_cli.auth.get_active_provider", return_value=None):
            assert _read_main_provider() == ""


class TestMainFallbackChainSkipSet:
    CHAIN = [
        {"provider": "openai-codex", "model": "gpt-5.4"},
        {"provider": "openai-codex", "model": "gpt-5.5"},
    ]

    def _run(self, main_tried):
        fake_client = MagicMock()
        with patch("hermes_cli.config.load_config", return_value={}), \
             patch("hermes_cli.fallback_config.get_fallback_chain",
                   return_value=self.CHAIN), \
             patch("agent.auxiliary_client._read_main_provider",
                   return_value="openai-codex"), \
             patch("agent.auxiliary_client._is_provider_unhealthy",
                   return_value=False), \
             patch("agent.auxiliary_client._task_minimum_context_length",
                   return_value=None), \
             patch("agent.auxiliary_client._resolve_fallback_entry",
                   return_value=(fake_client, "gpt-5.4")) as mock_resolve:
            result = _try_main_fallback_chain(
                "compression", "auto", reason="main provider unavailable",
                main_tried=main_tried)
        return result, mock_resolve

    def test_untried_main_provider_entries_are_used(self):
        """Step 1 never ran → same-provider fallback entries must be tried."""
        (client, model, provider), mock_resolve = self._run(main_tried=False)
        assert client is not None
        assert model == "gpt-5.4"
        assert provider == "openai-codex"
        mock_resolve.assert_called_once()

    def test_tried_main_provider_entries_are_skipped(self):
        """Step 1 ran and failed → same-provider entries stay skipped."""
        (client, model, provider), mock_resolve = self._run(main_tried=True)
        assert client is None
        assert model is None
        mock_resolve.assert_not_called()


class TestHonestUnhealthyReason:
    def test_reason_appears_in_log(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="agent.auxiliary_client"):
            _mark_provider_unhealthy("nous", ttl=1,
                                     reason="not configured — no authentication")
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "not configured — no authentication" in joined
        assert "payment / credit error" not in joined

    def test_default_reason_still_payment(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="agent.auxiliary_client"):
            _mark_provider_unhealthy("openrouter", ttl=1)
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "payment / credit error" in joined
