# Dropoff — 2026-07-21 10:48 Asia/Jerusalem

```
You are continuing work in telegram-personal-assistant on branch fix/personal-assistant-reliability.

## Current task & next step
Plan, but do not implement yet, a bounded Codex repair worker for Hermes. The watchdog already detects live Desktop tool failures quickly, but its repair feeder only creates a pending proposal; it does not launch a worker, verify a repair, apply it safely, or prove recovery on the live surface. The user explicitly asked for online research and a safety-first implementation plan before any code changes.

Next: finish official-source research, inspect the existing watchdog/improvement-supervisor seams, and write an implementation plan covering isolation, redaction, limits, verification, rollback, restart recovery, and visible status. Do not promise autonomous repair until it has been proven in the packaged Desktop runtime.

## Files touched / in flight
The worktree is intentionally very dirty: 80 tracked files changed (8,878 insertions, 526 deletions) plus multiple untracked files. These changes cover the broader Desktop + Telegram personal-assistant reliability rebuild and belong to prior/in-flight work; do not reset, absorb, or broadly stage them.

Files directly relevant to the latest watchdog lane include:
- scripts/hermes_live_watchdog.py
- systemd/hermes-live-watchdog.service.in
- plugins/improvement-supervisor/__init__.py and store.py
- tui_gateway/server.py
- tools/personal_assistant_tool.py
- apps/desktop/electron/main.ts and watchdog-service.ts/test.ts
- apps/desktop/src/store/personal-assistant.ts
- apps/desktop/src/app/chat/personal-assistant-situation.tsx/test.tsx
- corresponding watchdog, gateway, supervisor, and personal-assistant tests

No repair-worker implementation was started after the user's "just plan" instruction. This HANDOFF.md is the only file that should be committed by the dropoff.

## Key decisions & gotchas
- The watchdog did catch the screenshot's failures immediately. The missing layer is execution and verified recovery, not detection.
- Observed failures included missing timezone fields, identical patch strings, missing defer reasons/review dates, and missing context fields. The tool-loop guard warned but did not stop repeated attempts soon enough.
- The improvement supervisor created repair task t_73dc504f on board hermes-repairs, assigned to codex-repair, max_retries 1. The code requests initial_status="running", but no dispatcher call occurs and no PID/process is spawned; persisted display may show ready because of board semantics. The dedicated board has no proven continuous dispatcher, and the codex-repair profile does not exist.
- Do not dispatch that task as-is. Its saved repo root points to a stale flowstate-reliability-worktree, while the current packaged runtime uses this very dirty checkout. A normal worktree from HEAD would omit the uncommitted state that produced the incident.
- The current repair task body only prepares a tested repair and explicitly avoids merge/deploy/restart, so it cannot by itself satisfy the user's request to fix live failures.
- Proposed safety boundary to validate: one globally deduplicated worker; fixed-schema redacted incident; isolated snapshot of the actual running source state; workspace-write sandbox; approvals disabled; network off by default; strict time/resource limits; patch-only output; changed-file allowlist; source-digest check; independent verification; narrow apply/canary/rollback; persistent lifecycle state; orphan cleanup after restart.
- The read-only design audit found substantial reusable safety machinery already present: fixed-schema bounded incidents, privacy redaction, 64 KiB attachment cap, absolute-git-repo validation, fingerprinted repair worktrees, global deduplication, 30-minute runtime, one retry, and an explicit no-merge/deploy/restart/active-checkout-mutation contract. Preserve and test these instead of building a parallel launcher.
- Smallest candidate implementation to research and plan: create the repair task ready, run a dedicated concurrency-1 dispatcher tick for hermes-repairs, reuse the existing isolated spawn/log/run machinery, and continuously enforce runtime. Supervise and terminate the whole process group, not only the leader PID. Require a structured manifest plus independent tests; task completion text is not proof.
- Admission cleanup currently treats only done/archived as terminal; blocked, timed-out, or gave-up tasks may pin deduplication forever. The plan must cover terminal-state reconciliation, nonzero exits, timeout outcome reporting, and restart orphan recovery.
- The repair board should be a visible provenance/status mirror, not the sole execution mechanism unless a real dispatcher is explicitly configured and proven.
- Research primary sources before finalizing: official OpenAI Codex CLI noninteractive/sandbox/output/termination behavior, official systemd transient-unit and resource-hardening behavior, and official Git worktree/snapshot semantics. Evaluate the previously linked ksimback/looper repository only after these boundaries are clear; do not adopt it by default.
- Earlier focused verification passed: 119 relevant Python tests, 5 targeted TUI tests, and 3 Desktop startup tests; after the visible reliability-card work, 86 Python tests, 18 UI tests, typecheck, lint, and diff checks passed. A broader gateway suite still had 7 unrelated database-fixture failures. No tests were run for this dropoff.

## Env / run state
Last checked 2026-07-21 10:48 +03:00. Branch fix/personal-assistant-reliability; base tip ef4b2d6bc before this handoff commit.

The packaged Hermes Desktop and watchdog are running. Last observed main Desktop PID: 503136. Last observed watchdog PID: 503107. The watchdog service was previously proven active/enabled and Desktop startup was proven to start it after both had been stopped. Its heartbeat reported running with 11 watched sources.

The visible Personal Assistant reliability card and 10-second refresh were rebuilt and relaunched, but live automatic repair is not implemented or proven. A possible false-positive stale-consumer alert remains because the personal-assistant monitor is a 15-minute one-shot and has no owner between runs.

A read-only repair-worker design audit completed and its key findings are captured above.

Start by: inspect the existing repair-board dispatch and process-termination code, then browse the official Codex CLI, systemd transient-unit, and Git isolation documentation and turn those findings into a bounded implementation plan without modifying code.
```
