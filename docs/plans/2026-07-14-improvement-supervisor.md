# Hermes Improvement Supervisor

## Goal

Add the missing product/code improvement layer without duplicating Hermes'
existing background memory/skill review. The supervisor observes concrete
runtime failures and explicit user corrections, asks a bounded auxiliary model
to classify only those candidate turns, and stores deduplicated proposals for
human review.

## Cleanup and reuse plan

- Reuse the observer hook contract instead of adding hooks to the agent core.
- Reuse the host-owned plugin LLM facade instead of adding provider/auth code.
- Reuse profile-scoped `HERMES_HOME`, atomic JSON writes, and plugin slash
  commands instead of creating another service or model tool.
- Keep the current background review and curator unchanged; they remain the
  automatic memory/skill improvement layer.
- Add no dependency, no core model tool, no prompt-cache mutation, and no
  automatic Git, code, merge, deploy, restart, credential, or permission path.

## Authority matrix

| Action | Supervisor authority |
| --- | --- |
| Observe tool/API failures | Automatic |
| Inspect an explicit correction or failure turn | Automatic, bounded |
| Store a redacted, deduplicated proposal | Automatic |
| Repair a deterministic malformed tool input before display | Automatic, allowlisted |
| List/show/dismiss proposals | User command |
| Mark a proposal accepted for normal foreground work | User command |
| Edit source, prompts, policies, tests, or dependencies | Never |
| Commit, push, merge, deploy, restart, or change credentials | Never |

## Implementation

1. Add a standalone bundled plugin with `post_tool_call`,
   `api_request_error`, and `post_llm_call` observers.
2. Buffer only bounded failure metadata by turn. Redact credential-shaped
   values before inference or persistence.
3. Gate auxiliary analysis on a concrete signal: a failed tool/API attempt or
   explicit correction language. Ordinary successful turns cost nothing.
4. Run structured analysis in a daemon thread so the foreground response is
   never delayed. The output schema is a boolean decision plus category,
   confidence, stable deduplication key, evidence summary, and next check.
5. Persist proposals under the active profile with mode `0600`. A stable
   fingerprint merges recurrences; dismissed proposals remain latched.
6. Add `/improvements` commands for status, list, show, accept, dismiss, and
   clear-resolved. Acceptance changes backlog state only and explicitly tells
   the user that implementation must happen in a normal foreground task.

## Regression contract

- Successful ordinary turns do not invoke the auxiliary model.
- Failed tool/API events are bounded, redacted, and joined to the right turn.
- A qualifying turn starts at most one in-flight analysis per turn.
- Invalid, low-confidence, or malformed model output creates no proposal.
- Identical findings deduplicate and increment occurrence count.
- Dismissed findings never return to pending automatically.
- Profile homes remain isolated and stored files use private permissions.
- Hook/model/storage failures never break the foreground agent.
- No plugin path can write source, call Git, deploy, restart, or apply a patch.

## Verification

Run the focused plugin tests first, then observer/approval regression tests,
scoped Ruff, and plugin discovery checks. Completion requires reading the
outputs, not only process exit codes.

## Real-time recovery expansion

The first observed incident is a duplicated clarification choice. The cleanup
plan is deliberately narrow before adding broader autonomous repair:

1. Lock the existing clarify behavior with regression tests for exact and
   canonically Unicode-equivalent duplicate choices. Case and internal spacing
   remain significant because some choices are identifiers or commands.
2. Reuse the existing platform-neutral clarify normalization seam; preserve
   the first spelling and original order, and keep distinct choices unchanged.
3. Add `tool_request` middleware to the supervisor so an enabled supervisor
   repairs duplicate clarify arguments before tool-start events reach Desktop.
4. Persist a deterministic, privacy-safe high-confidence incident containing
   counts and stable identifiers only. Never store the question or choice text.
5. Keep runtime containment independent from durable code repair. A later
   repair worker consumes incidents out of process and must use a fresh isolated
   clone, bounded execution, tests, and draft output; it never touches the dirty
   running checkout or merges, deploys, restarts, changes credentials, or
   changes permissions automatically.

Regression constraints: plugin-disabled behavior remains safe through the core
clarify normalizer; plugin-enabled behavior additionally records the repaired
incident; distinct choices, callbacks, reconnect payloads, and free-text fallback
must remain unchanged.

## Restart-interrupted turn recovery

The next observed incident is a Desktop/profile-backend restart after a user
turn was accepted but before any assistant or tool row was persisted. The
transcript then ends in a user bubble while the resumed runtime reports idle.

1. Mark each accepted turn in profile-scoped working state with a timestamp and
   hash only; never persist prompt text in the recovery marker.
2. Clear the marker on every completed, failed, timed-out, or intentionally
   interrupted turn, including the immediate Stop path.
3. On cold resume, expose a recoverable turn only when the marker hash matches a
   non-empty user-only transcript tail. Any assistant/tool tail fails closed.
4. Reuse the existing bounded prompt submit path with its rewind ordinal so the
   orphan user row is replaced, not appended a second time.
5. Emit static recovery diagnostics without prompt text so the watchdog and
   supervisor can count detection, replay, and failure.

Regression constraints: intentional Stop never replays; completed sessions do
not replay; a turn with any persisted assistant/tool activity never replays;
recovery is one-shot per marker; profile state stays isolated; normal submit and
resume behavior remain unchanged.
