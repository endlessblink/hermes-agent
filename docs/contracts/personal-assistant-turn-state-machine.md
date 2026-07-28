# Personal Assistant turn state machine

Status: proposed contract for PA-STAB-002  
Owner: agent backend  
Consumers: Desktop gateway adapter, source adapters, renderer adapter

## Authority

The backend owns conversational state, accepted user-turn identity, current card
revision, context freshness, and the terminal outcome. Desktop owns only local
presentation and forwards typed events. Electron owns process lifecycle. Source
adapters own authoritative task, calendar, timer, and durable-memory readback.

## Phases

| Phase | Meaning | Permitted visible state |
|---|---|---|
| `idle` | No active turn | Composer ready |
| `submitting` | One accepted user turn is being dispatched | One pending user message |
| `restoring` | The runtime identity is invalid and recovery is bounded | One calm recovery state |
| `awaiting-context` | One consequential fact is missing or stale | One question |
| `planning` | Authoritative context is being reconciled and ranked | One preparation state |
| `awaiting-approval` | One mutation proposal awaits a decision | One approval |
| `completed` | The accepted turn has one terminal result | One result |
| `canceled` | The accepted turn was canceled without mutation | One cancellation result |
| `recoverable-failure` | The turn cannot continue automatically | One actionable recovery result |

## Stable identities

- `durableSessionId` identifies the visible, persisted conversation.
- `runtimeSessionId` identifies the current live backend runtime.
- `lineageRootId` identifies history that survives compaction or runtime replacement.
- `submissionId` is stable for one user intent across retries and recovery.
- `cardRevision` identifies the only card revision allowed to submit an action.

An ID is translated only at its authority boundary. A runtime rejected by the
backend enters the rejected set immediately and cannot be selected by any later
recovery rung.

## Events

| Event | Valid from | Transition or rule |
|---|---|---|
| `submit` | `idle`, `completed`, `canceled`, `recoverable-failure` | Accept a new `submissionId` once and enter `submitting` |
| duplicate `submit` | any | No state change and no second transcript row |
| `runtime-rejected` | `submitting`, `planning`, `awaiting-approval` | Record rejected runtime and enter `restoring` |
| `runtime-recovered` | `restoring` | Bind a non-rejected runtime and resume the same submission |
| `context-evaluated(stale=true)` | `submitting` | Enter `awaiting-context`; emit one question and no recommendations |
| `context-evaluated(stale=false)` | `submitting` | Enter `planning` |
| `context-recorded` | `awaiting-context` | Persist once, reconcile sources, then enter `planning` |
| `plan-ready` | `planning` | Increment the core-owned card revision and enter `completed` |
| `approval-required` | `planning` | Increment the core-owned card revision and enter `awaiting-approval` |
| `card-action` | `completed`, `awaiting-approval` | Accept only the active revision; stale revisions cause no action |
| `turn-failed` | any active phase | Enter one `recoverable-failure`; suppress raw transport text |
| `cancel` | any active phase | Enter `canceled`; perform no unapproved mutation |
| `turn-completed` | active phase | Enter `completed` exactly once |

## Invariants

- `PA-INV-01`: one visible tile has one durable lineage.
- `PA-INV-02`: one user intent has at most one accepted submission.
- `PA-INV-03`: a rejected runtime is never reused.
- `PA-INV-04`: one accepted turn has exactly one visible terminal outcome.
- `PA-INV-05`: raw transport errors never reach user-facing output.
- `PA-INV-06`: only the active card revision may submit an action.
- `PA-INV-07`: recommendations require authoritative source reconciliation.
- `PA-INV-08`: same-day context freshness uses the last meaningful user
  confirmation, never a generic state-update timestamp.
- `PA-INV-09`: stale consequential context yields one question before options.
- `PA-INV-10`: mutations require preview, approval, idempotency, and
  authoritative readback.

## Effects

The state machine requests effects but never performs them directly:

- recover or bind a runtime;
- persist accepted submission or context;
- refresh task, calendar, timer, and durable-memory authorities;
- rank eligible work;
- preview or apply an approved mutation;
- publish one renderer outcome;
- invalidate a superseded card.

Every effect returns a typed success or failure event. Adapter exceptions and
provider text are normalized before entering the state machine.

State persistence and requested effects commit atomically. Each pending effect
has a deterministic identity derived from its event identity and effect kind.
Replaying the same event returns the original receipt and effect identities
without appending another effect. Workers claim one effect under a bounded
lease; another worker cannot claim it before expiry, an expired lease is
recoverable after restart, and only the current lease owner may acknowledge
delivery. Delivery and the adapter's result event commit atomically, so a
process cannot persist “delivered” without also persisting the resulting phase
and next effects. Invalid result events leave the lease recoverable instead of
silently consuming the effect. Delivery acknowledgement is idempotent. Adapters
must also deduplicate their external action on `effectId`.

## Migration ownership map

| Existing owner | Current responsibility | Target boundary |
|---|---|---|
| Desktop prompt submission | Durable/runtime translation, retry, transcript append | Session adapter emits `submit`, `runtime-rejected`, and `runtime-recovered`; stable submission identity comes from the core |
| Desktop Personal Assistant routing | Open/focus canonical tile and bind returned runtime | Navigation adapter only; it cannot decide conversational phase |
| Desktop artifact renderer | Render and submit interview, planning, and approval cards | Renderer adapter receives one typed outcome and submits actions with `cardRevision` |
| Gateway Personal Assistant methods | Resolve owner profile, home, interview, state, and day-plan RPC | Transport adapter validates identity and forwards typed events/effect results |
| Durable Personal Assistant state store | Persist interview, source snapshot, approvals, and canonical session | Persistence adapter stores the core state revision atomically |
| Generic conversation loop | Currently mixes PA detection, interview continuation, planning, model work, fallback, and terminal repair | Ordinary-agent loop only; PA entry delegates once to the bounded core |
| Personal Assistant output gate | Validate model artifacts and build deterministic fallbacks | Outcome adapter validates content but cannot select the next phase |
| Calendar, FlowState, Notion, timer, and memory tools | Authoritative reads and approval-gated writes | Source/effect adapters returning normalized typed results |

The migration must reduce policy at each existing owner. It must not add a
second state machine in Desktop or duplicate source truth inside the core.

## Incident fixtures

`tests/fixtures/personal_assistant/incidents.json` is the initial replay corpus.
Each fixture preserves observed behavior, violated invariants, initial state,
events, and the required terminal result. Production routing cannot move to the
new core until all fixtures replay deterministically and generalized variations
cover fresh, long, restored, profile-switched, gateway-restarted, and
Desktop-restarted sessions.
