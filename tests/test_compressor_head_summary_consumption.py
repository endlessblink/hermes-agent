"""Prior handoff summaries must be consumed by compaction, never stacked.

The ratchet (2026-07-15, office-work): every gateway restart re-injects the
persisted handoff summary right after the system prompt. That head position
is protected, so each later compaction copied the old blob verbatim into the
output AND added a fresh summary. Real session reached 157k tokens per call
with a 70k-char "[PRIOR CONTEXT]" fossil at index 0/1 while `compression
done` reported tokens going UP (152 msgs ~164,284 -> 147 msgs ~164,435).

These tests pin the cure: old summary-bearing messages in the protected head
are folded into `_previous_summary` (existing behavior) and REMOVED from the
compacted output, so exactly one summary artifact survives a pass.
"""
from unittest.mock import patch

from agent.context_compressor import (
    SUMMARY_PREFIX,
    _MERGED_PRIOR_CONTEXT_HEADER,
    _MERGED_SUMMARY_DELIMITER,
    ContextCompressor,
)


def _comp():
    return ContextCompressor(
        model="m",
        threshold_percent=0.01,
        protect_first_n=1,
        protect_last_n=2,
        quiet_mode=True,
        config_context_length=4000,
        summary_mode="drop",
    )


OLD_TAIL_FOSSIL = "old pasted fossil content " * 200
OLD_SUMMARY_BODY = "## Active Task\nOld summarized state that must be rehydrated."


def _merged_head_blob():
    return (
        _MERGED_PRIOR_CONTEXT_HEADER
        + "\n"
        + OLD_TAIL_FOSSIL
        + "\n\n"
        + _MERGED_SUMMARY_DELIMITER
        + "\n\n"
        + SUMMARY_PREFIX
        + "\n"
        + OLD_SUMMARY_BODY
    )


def _standalone_head_summary():
    return SUMMARY_PREFIX + "\n" + OLD_SUMMARY_BODY


def _session(head_blobs):
    msgs = [{"role": "system", "content": "system prompt"}]
    for blob in head_blobs:
        msgs.append({"role": "user", "content": blob})
    for i in range(14):
        msgs.append({"role": "user", "content": f"question {i} " * 12})
        msgs.append({"role": "assistant", "content": f"answer {i} " * 12})
    return msgs


def _summary_artifacts(compacted):
    texts = [str(m.get("content", "")) for m in compacted]
    return [
        t for t in texts
        if SUMMARY_PREFIX[:40] in t or _MERGED_SUMMARY_DELIMITER in t
    ]


def test_head_merged_summary_blob_is_consumed_not_stacked():
    comp = _comp()
    msgs = _session([_merged_head_blob()])
    with patch("agent.context_compressor.call_llm"):
        out = comp.compress(msgs, current_tokens=100_000, force=True)

    blob_survivors = [
        str(m.get("content", "")) for m in out
        if OLD_TAIL_FOSSIL[:40] in str(m.get("content", ""))
    ]
    assert blob_survivors == [], "old merged blob fossilized in the head"
    assert len(_summary_artifacts(out)) <= 1, "summary artifacts stacked"
    # Continuity: the old summary body was rehydrated, not lost.
    assert OLD_SUMMARY_BODY.splitlines()[-1] in (comp._previous_summary or "")


def test_multiple_stacked_head_summaries_all_consumed():
    comp = _comp()
    msgs = _session([_standalone_head_summary(), _merged_head_blob()])
    with patch("agent.context_compressor.call_llm"):
        out = comp.compress(msgs, current_tokens=100_000, force=True)

    assert len(_summary_artifacts(out)) <= 1, "stacked summaries survived"


def test_compaction_reduces_tokens_with_head_summary_present():
    from agent.context_compressor import estimate_messages_tokens_rough

    comp = _comp()
    msgs = _session([_merged_head_blob()])
    before = estimate_messages_tokens_rough(msgs)
    with patch("agent.context_compressor.call_llm"):
        out = comp.compress(msgs, current_tokens=100_000, force=True)
    after = estimate_messages_tokens_rough(out)
    assert after < before, f"compression did not reduce tokens ({before} -> {after})"
