"""Memory-backed drop mode: compaction with no model call (Phase 1 real cure)."""
from unittest.mock import patch
import pytest
from agent.context_compressor import ContextCompressor


def _comp(mode):
    return ContextCompressor(model="m", threshold_percent=0.01, protect_first_n=1,
                             protect_last_n=1, quiet_mode=True, config_context_length=1000,
                             summary_mode=mode)

def _convo(n=20):
    m=[{"role":"system","content":"s"}]
    for i in range(n):
        m.append({"role":"user","content":f"msg {i} "*8}); m.append({"role":"assistant","content":f"reply {i} "*8})
    return m

def test_drop_mode_never_calls_the_model():
    comp=_comp("drop")
    with patch("agent.context_compressor.call_llm") as m:
        out=comp.compress(_convo(), current_tokens=10_000, force=True)
    m.assert_not_called()                 # instant — no model, no watchdog risk
    assert len(out) < len(_convo())       # old turns dropped from active window

def test_drop_mode_leaves_a_searchable_pointer():
    comp=_comp("drop")
    with patch("agent.context_compressor.call_llm"):
        out=comp.compress(_convo(), current_tokens=10_000, force=True)
    blob=" ".join(str(x.get("content","")) for x in out)
    assert "session_search" in blob        # tells the model the turns are recoverable

def test_llm_mode_still_calls_the_model():
    comp=_comp("llm")
    with patch.object(comp,"_generate_summary",return_value="SUMMARY") as g:
        comp.compress(_convo(), current_tokens=10_000, force=True)
    g.assert_called()
