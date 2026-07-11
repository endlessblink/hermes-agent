# Hermes reliability + memory lane — handoff (2026-07-11 08:14 Saturday)

You are continuing work in **hermes-agent** on branch **consolidate/single-tree**
(repo: `/home/endlessblink/.hermes/hermes-agent`). This is a long multi-phase effort to make
Hermes' memory/recollection reliable and stop a class of derailments the user hit on 2026-07-10.

## Current task & next step
Phase 6 (desktop UI bugs). Just fixed the sidebar hiding compacted chats — the compression-tip
projection copied the tip's (often blank) title, erasing the conversation's real title.
Fix applied in `hermes_state.py::list_sessions_rich` (title/preview now fall back to the root when
the tip is blank); verified on real film-maker data (4 of 6 rows now titled).
**Next: investigate why `list_sessions_rich` returns only ~6 rows for 36 film-maker sessions** —
the user says yesterday's un-archived chats still don't all show. Likely lineage over-collapse or a
page/limit/profile filter. Then rebuild the desktop app and have the user visually verify.

## The user's governing directive (2026-07-11)
**"The same chat must persist — not creating new ones."** This is already delivered going FORWARD by
two live config changes: `compression.in_place: true` (compact in the same session, scrollable) and
`compression.summary_mode: drop` (instant compaction, no model summary, watchdog can't fire). The
sidebar title fix is for chats that ALREADY rotated before this.

## Files touched / in flight
- `hermes_state.py` — UNCOMMITTED sidebar title-fallback fix (verified, safe). Commit it.
- Everything else committed (see git log): `agent/lane_resolver.py`, `agent/lane_recall.py`,
  `agent/lane_gate.py`, `agent/memory_capture.py`, `agent/memory_extraction.py`,
  `agent/context_compressor.py` (drop mode), `agent/agent_init.py`, `agent/turn_context.py`,
  `agent/tool_executor.py` (gate), `plugins/memory/holographic/*` (capture/recall/mirror),
  `tests/e2e/test_reliability_harness.py`, and many `tests/…`.
- Other modified files in `git status` (gateway/config.py, toolsets.py, hermes-ui-artifact*,
  vault_*) are the USER's own uncommitted WIP — DO NOT commit them as yours.
- User plugin (outside repo): `~/.hermes/plugins/obsidian-source-of-truth/__init__.py` — per-turn
  injection trimmed 1433→0 chars. Backed up in place.

## Key decisions & gotchas
- **NEVER force-kill (`pkill -9`) the running Hermes.** A hard restart earlier misrouted a chat to
  the wrong profile. Graceful stop; prefer letting the USER restart from the dock.
- **No live cloud LLM calls on the user's account** (their rule). Compression fix is proven only by
  wiring + the diagnostics log (`~/.hermes/logs/desktop-events.jsonl`, elapsed_seconds), not live.
- **Low VRAM** — never propose local Ollama for heavy/large-context tasks (summarization). Small
  CPU embeddings are fine, but Phase 4 recall used recency (no model) instead.
- **Run tests only via `./scripts/run_tests.sh`**, never bare `pytest tests/` (needs per-file
  isolation; bare pytest invents ~110 false failures). Use the tree's `.venv` (`./.venv/bin/python`).
- **Compression root cause (measured):** summaries ran the MAIN reasoning model, 45–241s, killed by
  a 12s watchdog → fresh sessions. Drop mode is the real cure (no summary at all).
- **Memory is currently OFF** (`memory.provider: ''`) to test compression cleanly. To enable:
  `hermes config set memory.provider holographic` + `plugins.hermes-memory-store.infer_facts true`.
- Config backups: `~/.hermes/config.yaml.bak-*`. Launcher (dock) is `~/.local/bin/hermes-desktop`
  (hardcodes the tree + `HERMES_COMPRESSION_WATCHDOG_SECONDS=60`).
- The single working tree is `~/.hermes/hermes-agent`; the old `-updated-…` worktree was removed.
- Task list (TaskCreate #1–7) tracks phases: 0–5 done, 6 (desktop UI) in progress. Fuller notes in
  the Claude memory files `hermes-e2e-lane`, `hermes-lane-resolver`, `hermes-single-tree`,
  `hardware-low-vram`, and the plan `/home/endlessblink/.claude/plans/answer-in-english-calm-locket.md`.

## Env / run state
Branch: consolidate/single-tree | Last commit: c3db03bb3 feat(compression): memory-backed drop mode
Live config staged (needs a restart to load drop mode): in_place=true, summary_mode=drop,
abort_on_summary_failure=true, aux.compression low reasoning effort, watchdog 60s, memory OFF.
Desktop app must be REBUILT (`cd apps/desktop && npm run pack`) for the sidebar fix to reach the UI.

Start by: commit `hermes_state.py`, then figure out why `list_sessions_rich` returns so few rows
for film-maker (36 sessions → ~6 rows) so yesterday's un-archived chats all appear.
