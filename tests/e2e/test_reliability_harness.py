"""Reliability harness for the Hermes memory/recollection lane (2026-07-10).

Encodes the acceptance criteria for the derailments observed on 2026-07-10, so
each phase's fix is proven, not hoped. Offline only — no live model calls
(measured compression timing lives in ~/.hermes/logs/desktop-events.jsonl and
is asserted separately when present).

Scenarios pinned here:
  - Compression must be able to run a *configured* (fast) model, and a failed
    summary must not drop the middle of the conversation.
  - A vague continuation must resolve to the real repo, not an old one.
  - On an unconfirmed project, the pre-action gate must refuse to act.
"""

from unittest.mock import patch

import pytest


REPO = "/home/endlessblink/.hermes/hermes-agent"


# ---------------------------------------------------------------------------
# Phase 1 — compression must not run the slow main model, must not drop context
# ---------------------------------------------------------------------------

class TestCompressionModelSelection:
    """MEASURED root cause: with no compression model configured, summaries run
    the main reasoning model and take 45-241s (desktop-events.jsonl), so the
    12s watchdog kills every substantial compaction -> the 'stuck / jumped to a
    fresh context' failures. The mechanism to fix it must honor a configured
    fast model."""

    def test_configured_compression_model_is_honored(self):
        from agent.auxiliary_client import _resolve_task_provider_model

        cfg = {"provider": "openai", "model": "gpt-5-mini", "base_url": "", "api_key": ""}
        with patch("agent.auxiliary_client._get_auxiliary_task_config", return_value=cfg):
            _, model, *_ = _resolve_task_provider_model(task="compression")
        assert model == "gpt-5-mini"

    def test_empty_config_falls_back_to_main_model(self):
        """This is the current production default -> the slow path. Documented so
        the fix (setting a fast model) is a visible, tested change."""
        from agent.auxiliary_client import _resolve_task_provider_model

        cfg = {"provider": "auto", "model": "", "base_url": "", "api_key": ""}
        with patch("agent.auxiliary_client._get_auxiliary_task_config", return_value=cfg):
            _, model, *_ = _resolve_task_provider_model(task="compression")
        assert model is None  # None => call_llm uses the main runtime (the slow model)


class TestCompressionNeverDropsMiddle:
    """A failed summary must leave the conversation intact, not replace the
    middle with a stub (the 'lost the task' failure)."""

    def _compressor(self, **kw):
        from agent.context_compressor import ContextCompressor

        return ContextCompressor(
            model="main-model",
            threshold_percent=0.01,
            protect_first_n=1,
            protect_last_n=1,
            quiet_mode=True,
            config_context_length=1000,
            **kw,
        )

    def _big_convo(self, n=30):
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(n):
            msgs.append({"role": "user", "content": f"user message number {i} " * 10})
            msgs.append({"role": "assistant", "content": f"assistant reply number {i} " * 10})
        return msgs

    def test_abort_on_failure_preserves_every_message(self):
        comp = self._compressor(abort_on_summary_failure=True)
        convo = self._big_convo()
        with patch.object(comp, "_generate_summary", return_value=None):
            out = comp.compress(convo, current_tokens=10_000, force=True)
        assert out == convo
        assert getattr(comp, "_last_compress_aborted", False) is True

    def test_without_abort_the_middle_is_dropped(self):
        """Documents the current default (data loss) so flipping the flag is a
        deliberate, tested change."""
        comp = self._compressor(abort_on_summary_failure=False)
        convo = self._big_convo()
        with patch.object(comp, "_generate_summary", return_value=None):
            out = comp.compress(convo, current_tokens=10_000, force=True)
        assert len(out) < len(convo)  # middle collapsed into a fallback marker


# ---------------------------------------------------------------------------
# Phase 3 — vague continuation resolves to the real repo, gate refuses to guess
# ---------------------------------------------------------------------------

class TestWrongRepoContinuation:
    def _codex_history(self):
        return [
            {
                "role": "assistant",
                "content": (
                    f"```bash\ncodex exec --cd {REPO} --sandbox workspace-write - "
                    "< /tmp/codex-flowstate-primitives-prompt.md\n```"
                ),
            },
            {"role": "user", "content": "codex finished, can you check?"},
        ]

    def test_resolves_to_the_real_repo_not_an_old_one(self):
        from agent.lane_resolver import resolve_lane

        lane = resolve_lane(self._codex_history(), session_cwd="/home/endlessblink")
        assert lane is not None
        assert lane.repo_path == REPO
        assert "html-video" not in lane.repo_path


class TestGateRefusesToGuess:
    def _armed_agent(self):
        import types
        return types.SimpleNamespace(_lane_gate_armed=True, _lane_gate_satisfied=False)

    def test_write_blocked_on_unconfirmed_project(self):
        from agent import lane_gate

        assert lane_gate.gate_block_message(self._armed_agent(), "write_file", {"path": "x"}) is not None

    def test_reads_for_evidence_and_clarify_stay_open(self):
        from agent import lane_gate

        agent = self._armed_agent()
        assert lane_gate.gate_block_message(agent, "clarify", {}) is None
        assert lane_gate.gate_block_message(agent, "session_search", {}) is None
        assert lane_gate.gate_block_message(agent, "terminal", {"command": "git status"}) is None


# ---------------------------------------------------------------------------
# Measured evidence guard — compression really did run for minutes tonight
# ---------------------------------------------------------------------------

class TestMeasuredCompressionEvidence:
    def test_diagnostics_show_minutes_long_summaries_when_present(self):
        """Not a pass/fail gate on behaviour — a guard that our root-cause
        measurement is reproducible from the log while it exists."""
        import json
        import os

        path = os.path.expanduser("~/.hermes/logs/desktop-events.jsonl")
        if not os.path.exists(path):
            pytest.skip("diagnostics log not present on this machine")
        elapsed = []
        for line in open(path):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            d = e.get("details") if isinstance(e.get("details"), dict) else {}
            v = (d or {}).get("elapsed_seconds")
            if isinstance(v, (int, float)):
                elapsed.append(v)
        if not elapsed:
            pytest.skip("no compression elapsed_seconds recorded")
        # The root-cause claim: summaries routinely blow past the 12s watchdog.
        assert max(elapsed) > 30, "expected minutes-long summaries in the evidence"
