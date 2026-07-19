# Dropoff — 2026-07-19 19:46 Sunday

```
You are continuing work in hermes-agent (~/.hermes/hermes-agent, the LIVE install) on branch main.

## Current task & next step
Design a reliable fix for blocking compaction pauses ("Summarizing thread", measured 112-235s,
median ~217s, re-fires every ~10-30 min in the office-work profile) — next: finish the PLAN
(user demanded deep research, NO ad-hoc changes), then implement task #35 after approval.
A plan-mode session was interrupted mid-research; the plan file was never written
(/home/endlessblink/.claude/plans/snuggly-baking-lecun.md is empty/absent).

Research already gathered (reuse, don't redo):
- Measured: compaction succeeds 8/8 since Jul-18 but blocks 2-4 min; residue after compaction
  was ~92k of ~120k because protect_last_n kept giant tool results (now 10, was 20, all 9 configs).
- Industry patterns (Claude Code/Codex/OpenCode/Amp — see gist.github.com/badlogic/cd2ef65b0697c4dbe2d13fbecb0a0a5f
  and codex.danielvaughan.com compaction deep dives): (1) trim/offload BULKY TOOL RESULTS first
  (microcompaction — cheap, no LLM); (2) keep summaries small + prompt-cache-friendly (delete
  less, keep prefix stable); (3) offload big outputs to files with path references (near-lossless;
  Hermes already has archived rows + FTS recall to lean on); (4) parallel/async compaction exists
  in research, but tool-result trimming is the proven cheap win.
- Candidate insertion point (verified): gateway _compress_session_history (tui_gateway/server.py
  ~3590) + session.compress RPC (~9882) snapshots history WITHOUT holding history_lock during the
  LLM call and has history_version conflict detection — i.e. a post-turn (after message.complete)
  trigger at ~90% threshold can reuse it; pre-API compaction stays as backstop (task #35).
- Machinery map COMPLETE — read docs/superpowers/specs/2026-07-19-compaction-nonblocking-map.md
  (call graph, locks/invariants, the 217s anatomy, ready-made seams incl. partial_compress.py
  and the turn_finalizer post-response background pattern). Do NOT re-explore.

## Files touched / in flight
All committed & pushed (tip 699e8a703). No uncommitted changes. Key recent work:
- tui_gateway/server.py (compression watchdog derives from aux budget), agent/auxiliary_client.py
  (unconfigured providers 1h skip), agent/context_compressor.py (4k summary ceiling — DO NOT
  change without fresh measurements, see memory no-blind-tuning), agent/prompt_builder.py
  (skills-index cap; day-timeline + multi-choice + timeline guidance), apps/desktop/src/*
  (folder menu+drag, PA button, profile restore, stale-clarify re-queue, queue toasts,
  time column, day-timeline prompt support, description 1200).
- Configs (outside git, all 9 = base + 8 profiles): reasoning_effort low, tool_search auto
  (REVERTED from on — deferral cost LLM rounds), compression.threshold 0.35, protect_last_n 10.
  Backups: *.bak-toolsearch-20260717, *.bak-compthresh-20260717.

## Key decisions & gotchas
- MEMORY FILES ARE CURRENT — read MEMORY.md first: hermes-speed-lane (full state),
  upstream-merge-postmortem-20260718 (mandatory next-merge checklist), no-blind-tuning
  (NEVER retune constants without fresh measurements — user exploded over this),
  merge-preserve-all-features.
- SINGLE WRITER: another Claude/codex session edits this tree sometimes (it cherry-picked
  77f77f1cf and switched branches mid-day). Check `git branch --show-current` + `git status`
  before EVERY commit; commits once landed on a stray branch (fix/desktop-custom-time-picker,
  since merged).
- The app re-serializes config.yaml (YAML `on` becomes `true`) — match both spellings when editing.
- Desktop changes need: `npm run pack --workspace apps/desktop` from REPO ROOT (root install only;
  an apps/desktop-local node_modules breaks the build), then the USER must quit+reopen the app.
  Backend/prompt changes need `systemctl --user restart hermes-gateway.service` + app relaunch.
- Several "bugs" were stale-build artifacts — ALWAYS verify the running app/process start time
  vs the asar/commit timestamp before debugging (ps lstart vs stat app.asar).
- rtk wraps grep/ls and mangles output — use awk/sed or ctx_execute for parsing.
- Pre-existing red tests (NOT ours): desktop-fs picker (2), approval-group clarify-card (1),
  skills index isolation (7 with full suite), tui_gateway env-sensitive family (5-13 by env).
  CI: footguns now green; js-autofix gated off the fork.
- User rules: responses 1-4 plain sentences + Next steps (hook enforces); no live cloud LLM
  calls for testing; superpowers skills on demand; investigate before ANY fix.

## Env / run state
Branch: main | Last commit: 699e8a703 feat(desktop+prompt): day-timeline + regression nets
Running: hermes-gateway.service (restarted 16:10+ with all fixes), user's desktop app
(restarts frequently — verify build stamp), FlowState app + local Supabase docker (healthy),
hermes-live-watchdog.service, PA monitor timer (15-min, healthy).
Open tasks: #32 verify speed settings live, #33 verify behavior rules, #34 verify cross-profile
answers (SOUL rule added — session_search profile param, never raw sqlite), #35 post-reply
compaction (THE next implementation, plan first).
User-confirmed working: queue-while-busy, custom form answers, folder drag+menu, profile
switching, PA button, Google calendar access.

Start by: reading docs/superpowers/specs/2026-07-19-compaction-nonblocking-map.md, then
write the plan for #35 (post-message.complete trigger at ~90% threshold reusing
_compress_session_history, PLUS tool-result trimming/offload as the likely bigger win) and
present it for approval before touching anything.
```
