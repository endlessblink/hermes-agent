# Compaction non-blocking design map (explored 2026-07-19, for task #35)

Facts only — seams that exist today. Measured problem: auto-compaction blocks the turn
112-235s (median ~217s), re-fires every ~10-30 min on office-work.

## Where compaction runs (all synchronous, on the turn thread)
- Pre-API preflight: agent/conversation_loop.py:1161 (guard block 1074-1161)
- Mid-loop post-tool-round: conversation_loop.py:5147 (block 5124-5152)
- Error-recovery (overflow/413): conversation_loop.py:3321, 3392, 3647, 3888 (max 3 attempts)
- Also agent/turn_context.py:538; gateway _compress_session_history → agent._compress_context
- Real body: agent/conversation_compression.py:compress_context (def :578)

## State/invariants any background variant must honor
- state.db compression lock keyed on OLD session_id (conversation_compression.py:712, ~760-830)
  + lease refresher thread _CompressionLockLeaseRefresher (:218, refresh :276)
- history_version optimistic check + bump (tui_gateway/server.py:3632-3638)
- Post-compaction resets: conversation_history_after_compression (loop:1184/3397/5107;
  in-place → list(messages), rotation → None), last_prompt_tokens=-1 sentinel (loop:5133),
  compression_attempts resets (loop:644,1258,1642,1715,1877,2053,3508,3514)
- session["running"] + durable queued_turn machinery means prompts arriving mid-compaction queue.

## The 217s anatomy
- Single NON-STREAMING sync call_llm(task="compression") — context_compressor.py:2308,
  kwargs 2278-2300, NO max_tokens (deliberate), stream defaults False (call_llm supports
  stream=True — only MoA uses it; async to_thread adapters exist unused: auxiliary_client.py:1266+)
- Input: full pre-tail transcript, per-message cap 6000 chars (_CONTENT_MAX:1758,
  _CONTENT_HEAD 4000), tool results pre-summarized (:674), args truncated 200 chars (:484)
- Rolling summary: _previous_summary means each pass summarizes only NEW turns (2242-2252)
- Summary budget: min(5% ctx, 4000) prompt-guidance only (:244, :1346)
- Compression timeout capped by _effective_aux_timeout; same-provider retry skipped on timeout.

## Ready-made seams for the fix
1. Gateway session.compress already runs on the RPC thread pool (_LONG_HANDLERS :183-234,
   pool :246) and _compress_session_history (:3590) snapshots history and RELEASES history_lock
   during the LLM call, with version-conflict abort — the natural host for post-reply compaction.
   Caveat: it rejects when session running (:9909) — a post-message.complete trigger runs when
   running=False, so that's compatible.
2. turn_finalizer.finalize_turn (:191) already spawns post-response background work (memory
   extraction :140@memory_extraction, background review turn_finalizer:724, title gen) —
   the established "after the reply, off-thread" pattern to imitate.
3. Partial compaction primitives exist: hermes_cli/partial_compress.py (split head/tail,
   compress head only, rejoin — tests exist), focus compaction, tail token budget,
   image shrinking, anti-thrash guard (<10% savings twice → cooldown :1526-1534).
4. Desktop UX already queues (not rejects) sends during compacting (composer sendBlocked;
   busyAction 'queue'), and tracks compacting state via status.update kind=compacting
   (store/compaction.ts; gateway-event.ts:739).

## Industry patterns (research links)
- Trim/offload bulky tool results BEFORE any LLM summary (microcompaction) — biggest cheap win;
  Hermes already pre-shrinks them for the SUMMARIZER INPUT but not in the LIVE transcript.
- Keep summaries small; prompt-cache-friendly deletion (stable prefix).
- Offload large outputs to files/archive with references (Hermes has archived rows + FTS recall).
- Sources: gist.github.com/badlogic/cd2ef65b0697c4dbe2d13fbecb0a0a5f;
  codex.danielvaughan.com 2026-04-14 compaction deep dive; arxiv 2605.23296 (parallel compaction);
  platform.claude.com/docs/en/build-with-claude/compaction.
