# Personal Assistant stabilization lane

Date: 2026-07-26  
Status: active  
Branch: `fix/personal-assistant-reliability`

## Objective

Make the Personal Assistant one coherent, state-aware conversation on the real
packaged Desktop. It must recover its canonical session, reconcile live task
state and recent user progress, ask one useful question when context is stale,
and only then offer grounded actions. Raw transport errors, duplicate turns,
stale actionable cards, and repeated option dumps are release blockers.

This lane replaces reactive screenshot-by-screenshot repair. No new Personal
Assistant feature work begins until this lane passes its packaged proof gate.

## Architecture decision

Stay in Hermes, but rebuild the Personal Assistant orchestration core behind a
single typed boundary. Do not rewrite the whole agent and do not keep extending
the current overlapping paths.

Hermes remains responsible for the capabilities that already work and would be
expensive and risky to recreate: packaged Desktop lifecycle, profile gateways,
canonical session storage, streaming transport, Calendar/FlowState/Notion
connectors, authentication, approval-gated mutations, durable state, monitoring,
and update/restart behavior. The new Personal Assistant core owns only:

- conversational state and transitions;
- context freshness and reconciliation;
- question selection;
- planning eligibility and ranking inputs;
- visible terminal outcome selection;
- idempotency keys for user turns and card actions.

The current code contains approximately 14,655 lines across Personal Assistant
and adjacent session production paths, approximately 14,491 lines of related
tests, and at least 276 directly matching test cases. This is strong reusable
infrastructure and proof capital, but it also makes a full rewrite slower to
reach trustworthy parity. The primary coupling hotspot is the generic
conversation loop, where Personal Assistant fast paths, interview continuation,
planning, retries, compaction, output repair, and terminal recovery currently
overlap. The bounded rebuild removes Personal Assistant policy from that loop
incrementally while retaining the surrounding Hermes runtime.

Promotion uses a strangler transition:

1. Specify the new state machine and adapter contracts.
2. Replay saved incidents against it without changing production routing.
3. Route the generated acceptance profile through the new core.
4. Compare old and new traces, but expose only the new core's result.
5. Route the real `office-work` profile after the generated profile passes.
6. Delete superseded Personal Assistant branches only after packaged parity and
   restart proof.

Do not create a new repository or standalone agent unless PA-STAB-002 proves
that the required Hermes adapters cannot be separated without changing their
public contracts. Current evidence does not support that exception.

## Scope authority

The exhaustive acceptance ledger remains
`docs/plans/2026-07-21-personal-manager-e2e-matrix.md`. New failures must be
recorded there before repair. This document defines the execution order for the
current stabilization checkpoint.

## Current incident

The 2026-07-26 headed packaged Desktop showed:

- `Prompt failed` and `session not found` exposed directly to the user;
- the same request present more than once;
- old option cards left visible and actionable after failure;
- a new list of options produced without first reconciling completed work or
  asking what changed;
- a source warning mixed into the plan while the conversation remained in an
  indeterminate state.

Two regression-first patches exist in the dirty tree:

- rejected runtime sessions no longer qualify as their own recovery target;
- a same-day plan older than fifteen minutes can request one progress review,
  and named completed work is excluded from the next shortlist.

These are candidates, not completed fixes. The session patch passed its focused
Desktop tests and one packaged retry without a visible raw error. The
progress-review patch passed Python tests but has not passed the packaged
Desktop flow after a clean backend and Desktop restart.

## Non-negotiable invariants

1. One visible Personal Assistant tile maps to one canonical durable lineage.
2. One user submission creates at most one accepted turn.
3. A backend-rejected runtime is never retried as recovery.
4. Every accepted turn reaches exactly one visible terminal state within the
   latency budget.
5. Transport and source failures become one calm, actionable recovery state;
   raw backend strings never appear in the transcript or toast.
6. Superseded cards are disabled or replaced and cannot submit stale actions.
7. Planning first reconciles authoritative task status, calendar state,
   persisted context, and recommendation history.
8. If same-day conversational context is stale, Hermes asks exactly one
   progress question before offering options.
9. Known current facts are reused; unknown consequential facts are asked once.
10. Every option names the task and explains the current reason it fits.
11. No source mutation occurs without the existing preview, approval,
    idempotency, and authoritative-readback gates.
12. A full Desktop restart must preserve all preceding invariants.

## Task lane

### PA-STAB-001 — Capture a deterministic failing baseline

Status: in progress

Captured: the supplied screenshot is preserved with SHA-256
`3ce47d86ea30aee51560eee34cd87e4c3d45d079c2c5abf568e036148b09d890`;
four separate replay fixtures now cover rejected runtime reuse, duplicate
submission, stale actionable options, and stale conversational context.
Remaining: reproduce the four contracts through the generated headed acceptance
profile and capture backend/session identities for each.

- Save the supplied screenshot and hash in the evidence folder.
- Record build identity, visible tile, stored session, runtime session, owner
  profile, backend PID, and durable lineage.
- Reproduce with a generated Noam acceptance profile and then the real
  `office-work` profile.
- Preserve the exact transcript and backend trace.

Done when the raw error, duplicate submission, stale-card behavior, and
premature options are each reproducible or explicitly separated into distinct
causes.

### PA-STAB-002 — Specify one conversation state machine

Status: complete

Implemented but not routed: the typed phase/event/effect contract, migration
ownership map, a pure reducer replaying the four incident fixtures, strict
transition guards, core-owned card revisions, and durable compare-and-swap
persistence in the existing Personal Assistant state authority. State changes
and deterministic pending effects now commit atomically; duplicate events return
their original receipt and effect identities, conflicting event identities fail
closed, and concurrent writers cannot both accept a submission. The durable
outbox now has exclusive bounded claims, expired-lease recovery after restart,
lease-owner delivery acknowledgement, and idempotent completion. A narrow
adapter runner now commits effect delivery, the normalized adapter result event,
and any next effects in one atomic state write; invalid results remain
recoverable. Typed shadow ports now cover session, context, source, action, and
renderer effects. A generated shadow journey asks one progress question,
persists its answer, reconciles sources once, and publishes exactly one
named-task plan. The core now persists the bounded user intent with its
submission identity and forwards it explicitly to source reconciliation, so
production adapters never have to reconstruct intent from transcript state.
The public state contract now exposes only the safe current turn projection,
and Desktop accepts it additively. Session recovery no longer resubmits the user
prompt: it preserves and republishes the exact persisted card revision after a
new runtime is recovered. Concrete shadow context and source adapters now read
the durable interview once, fail closed unless readiness is approved, and wrap
the existing validated planning boundary without bypassing calendar or output
gates. Fifty-four focused core/adapter/runner/incident tests pass. The wider
The single-instance shadow worker is implemented with an explicit generated
acceptance-profile gate, bounded polling, stable worker identity, clean
shutdown, and fail-closed error capture; it refuses `office-work`. Fifty-six
focused tests pass, and the wider Personal Assistant suite passes all 417
tests. The production adapters, generated-profile lifecycle, Desktop routing,
profile-scoped UI cache, visible active-turn renderer, and direct progress
answer action are connected. A headed packaged run rendered the generated
profile's single progress question without real-profile context, persisted its
answer exactly once, and transitioned to a bounded recovery outcome after the
pre-restart runtime was unavailable. Seventy-four focused backend tests and
fifty-four focused Desktop tests pass. The apparent interview
snapshot-refresh failure was a stale test: capture-time-only changes
intentionally preserve the active card revision, while material calendar
changes refresh the snapshot without losing answers. Restart recovery and the
subsequent grounded-plan proof continue under PA-STAB-003 and PA-STAB-004.

- Inventory every current owner of Personal Assistant state: Desktop routing,
  prompt submission, gateway session recovery, planning interview, output gate,
  renderer, and durable profile state.
- Define a single transition table for idle, submitting, awaiting-context,
  planning, awaiting-approval, completed, canceled, recoverable-failure, and
  restoring.
- Define typed input, state, event, effect, and visible-outcome contracts for a
  new bounded Personal Assistant core.
- Keep source reads, mutations, session persistence, and rendering behind
  adapters owned by Hermes.
- Identify overlapping legacy branches for later deletion only after regression
  coverage locks their required behavior.

Done when every event has one owner, one persisted transition, and one visible
representation, and saved incidents can be replayed deterministically through
the new core without a live model or Desktop.

### PA-STAB-003 — Make session recovery deterministic

Status: in progress, unproved

- Keep the regression for a locally cached runtime that the backend has already
  rejected.
- Add variations for fresh, restored, long, profile-switched, gateway-restarted,
  and full-Desktop-restarted conversations.
- Prove exactly-once submit and transcript append through each recovery.
- Remove duplicate inline and toast reporting for the same failure.

Done after two clean packaged restart passes with no raw error, duplicate turn,
wrong tile, or stale runtime retry.

### PA-STAB-004 — Reconcile reality before recommendations

Status: in progress, unproved

- Separate “user context last confirmed” from generic interview/state update
  timestamps.
- Read authoritative task status before ranking and omit completed, canceled,
  deleted, or otherwise ineligible work.
- When current-day context is stale, ask one short progress question; accept
  task names or “nothing,” persist the answer, reconcile matching tasks, then
  continue automatically.
- Never turn the progress answer into an unapproved source mutation.
- Verify changed calendar events, active timers, and recent recommendation
  history before reranking.

Done when the exact Hebrew request first asks what changed when necessary, then
produces new grounded options that agree with all authoritative readbacks.

### PA-STAB-005 — Make failure and stale UI states safe

Status: planned

- Collapse one incident into one visible recovery message.
- Disable superseded option and interview cards.
- Ensure retry resumes the same durable turn and cannot replay an accepted
  answer or action.
- Unlock the composer within ten seconds after failure or cancellation.

Done when fault injection at submit, retrieval, planning, delivery, and
rehydration produces no raw internals and the next request works immediately.

### PA-STAB-006 — Simplify the first conversational screen

Status: planned

- Show one question or at most three options, never both.
- Keep source coverage and limitations behind progressive disclosure unless a
  limitation blocks safe planning.
- Show one primary action per option and one plan-level adjustment action.
- Remove repeated controls and historical actions that no longer apply.

Done after a visual review on fresh, long, failed, and restored transcripts at
the actual window size.

### PA-STAB-007 — Build the reusable acceptance harness

Status: planned

- Drive the headed packaged Desktop through CDP without taking user input.
- Generate task names, dates, statuses, calendars, source failures, session
  replacements, and restart points.
- Capture visible output, backend/tool trace, source readback, durable-state
  readback, latency, model calls, retries, and duplicate submissions.
- Make each incident replayable from a saved fixture without the user's real
  task data.

Done when every invariant above has an exact regression and a generalized
variation.

### PA-STAB-008 — Prove and promote

Status: blocked by PA-STAB-001 through PA-STAB-007

- Run lint, typecheck, focused tests, full relevant tests, static analysis, and
  package build.
- Run ten consecutive clean Hebrew journeys on the generated acceptance
  profile.
- Run two consecutive clean journeys on the real `office-work` profile, each
  separated by a full packaged Desktop restart.
- Preserve evidence and exact state rollback for every real-profile run.

Any raw error, duplicate turn, stale action, wrong recommendation, missing
source, false claim, stuck state, latency violation, or restart failure resets
the consecutive count to zero.

## Execution rules

- Work one task ID at a time in the order above.
- Start each repair with a failing regression.
- Do not combine session recovery, planning behavior, and visual cleanup into
  one unreviewable change.
- Do not claim success from unit tests, source inspection, or one clean run.
- Preserve the user's unrelated dirty changes.
- Update the acceptance ledger and this lane after every packaged proof run.
- If the same invariant fails three times under different symptoms, stop
  patching and replace the responsible state boundary before continuing.

## PA-STAB-002 recovery-boundary cleanup

Before production adapters are connected, remove the shadow core's
`dispatch-submission` recovery effect. Planning and source reconciliation remain
backend-owned; a recovered runtime is only a presentation destination.

Lock the current replay, lease, and idempotency behavior with regressions, then:

- preserve the exact persisted visible outcome across runtime rejection;
- recover a non-rejected runtime without re-submitting the user prompt;
- retry publication of that same card revision exactly once;
- keep source reconciliation and approved mutations outside session recovery;
- delete the obsolete submission-dispatch adapter branch after replacements pass.

## Immediate restart point

`$next-fast-track` must resume this lane in the following order:

1. **PA-STAB-002:** wire the implemented profile-gated shadow worker into the
   generated acceptance-profile lifecycle; it must remain impossible to start
   for `office-work`.
2. **PA-STAB-003:** prove fresh, restored, stale-runtime, duplicate-submit,
   gateway-restart, and full-Desktop-restart session journeys.
3. **PA-STAB-004:** prove task/calendar/timer/context/recommendation
   reconciliation before ranking.
4. **PA-STAB-005:** atomically replace stale cards and collapse failures into one
   safe recovery outcome.
5. **PA-STAB-006:** simplify the visible conversation after correctness passes.
6. **PA-STAB-007:** complete the reusable headed packaged acceptance harness.
7. **PA-STAB-008:** run consecutive generated and real-profile promotion gates,
   then delete superseded orchestration.

The active immediate action is PA-STAB-002. Do not enable the real
`office-work` route until the generated profile passes its headed shadow
journey.
