# Hermes-FlowState Complete Reliability Lane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Hermes interaction with FlowState and Notion complete, scoped, approval-bound, retry-safe, canonically verified, causally monitored, and proven in the packaged desktop applications.

**Architecture:** VPS Supabase remains the canonical signed-user authority. FlowState owns domain commands and durable receipts; its Electron Local Task API exposes the same commands without inventing weaker sidecar semantics; Hermes validates those receipts, renders compact interactive decisions, and never converts a connector failure into follow-up writes. Realtime, IPC, caches, monitor events, and local files are hints or offline projections, never proof of committed state.

**Tech Stack:** PostgreSQL/Supabase RLS and RPCs, FlowState Vue/Electron/Node Local Task API, Hermes Python tools and monitor, Hermes Desktop React/TypeScript `hermes-ui`, Vitest, pytest, rollback-only SQL, Electron/AppImage release tooling.

---

## Lane rules and dependency order

Every task in this lane is **P0 / HIGH** because it prevents false success, lost updates, wrong-scope mutations, self-interruption, or unprovable packaged behavior.

```text
H0 truth ledger ─┬─> H1 auth recovery ─> H2 complete reads ─────────────┐
                 └─> H3 canonical receipt foundation ─> H4-H7 writers ├─> H10 assistant UI
H2 + H3 ────────────────────────────────> H8 monitor causality ─────────┤
H3 ─────────────────────────────────────> H9 writable Notion ──────────┘
H1 through H10 ─────────────────────────> H11 packaged ship/watchdogs
```

The lane is complete only when the public artifact, installed bytes, live processes, protected reads, and exact read-backs agree. A version string, HTTP 200, optimistic renderer state, queued offline intent, or source-only test is never sufficient proof.

## Verified baseline and current gaps

- Complete inventory, exact task reads, canonical scalar patch, Notion activation, recurring `Done for now`, and duplicate merge foundations exist.
- Task create/delete, work-block scheduling, subtask mutation, timer control, projects/groups/lanes, and Canvas operations do not yet share one canonical receipt contract.
- Search and filtered list are capped samples; only inventory may claim completeness.
- Monitor production still reads a 25-row sample and lacks canonical operation/source identity for causal suppression.
- General Notion create/property/status/archive writes are not exposed; activation is intentionally separate.
- Packaged auth recovery and release/install/live truth remain open proof boundaries.
- The live watchdog now rejects defunct FlowState processes and restores the desktop launcher/X authority, but a desktop session whose X server cannot create renderer windows must be reported as a distinct runtime failure, not as a healthy app.

### Task H0: Build one machine-readable source-to-live truth ledger

**Files:**
- Create: FlowState `scripts/audit-assistant-release-state.cjs`
- Create: FlowState `tests/unit/scripts/assistant-release-state.test.ts`
- Modify: FlowState `scripts/deploy-electron-update.sh`
- Modify: FlowState `docs/MASTER_PLAN.md`
- Create: Hermes `scripts/audit_flowstate_runtime.py`
- Create: Hermes `tests/test_audit_flowstate_runtime.py`

- [ ] **Step 1: Write failing fixtures for contradictory states**

The fixture must distinguish these states without reading secret values:

```json
{
  "source": {"commit": "40-hex", "dirty": false},
  "database": {"migration": "20260715010000", "rpcPresent": true},
  "publicRelease": {"version": "1.4.262", "sha512Matches": true},
  "installed": {"sha512MatchesPublic": true, "processUsesInstalledBytes": true},
  "renderer": {"started": true, "canSyncRemotely": true},
  "sidecar": {"health": true, "protectedRead": true, "appVersionMatches": true},
  "hermes": {"sourceCommit": "40-hex", "packageCommit": "40-hex", "gatewayCommit": "40-hex"},
  "verdict": "verified"
}
```

Fixtures with a missing renderer, stale ASAR, public/local checksum mismatch, health-only sidecar, or absent migration must return `verdict: "blocked"` plus stable reason codes.

- [ ] **Step 2: Run RED tests**

Run: `npm test -- tests/unit/scripts/assistant-release-state.test.ts`
Expected: FAIL because the auditors do not exist.

- [ ] **Step 3: Implement redacted auditors**

The scripts may report booleans, versions, commit hashes, migration names, process IDs, ports, counts, and checksums. They must never print tokens, headers, cookies, environment values, task titles, or raw process command lines.

- [ ] **Step 4: Gate release completion on the ledger**

`deploy-electron-update.sh` must write the source/build/public portion. Installed and protected-live proof remain post-install steps and cannot be pre-filled.

- [ ] **Step 5: Verify and commit**

Run: `npm test -- tests/unit/scripts/assistant-release-state.test.ts && python -m pytest -q tests/test_audit_flowstate_runtime.py`
Expected: all tests pass and a contradictory fixture is blocked.

Commit: `test(release): add assistant source-to-live truth ledger`

### Task H1: Close cold-restart authentication and protected sidecar recovery

**Files:**
- Modify: FlowState `src/stores/auth.ts`
- Modify: FlowState `src/composables/useLocalApiBridge.ts`
- Modify: FlowState `electron/ipc/localApi.ts`
- Modify: FlowState `server/local-api/auth-availability.cjs`
- Modify: FlowState `scripts/diagnose-live-boundary.cjs`
- Test: FlowState `tests/unit/restore-auth-backup.test.ts`
- Test: FlowState `tests/unit/local-api/auth-availability.test.ts`
- Test: FlowState `tests/unit/electron/local-api-lifecycle.test.ts`

- [ ] **Step 1: Lock the five auth states in tests**

```ts
type ProtectedAuthState =
  | 'authenticated'
  | 'refreshing'
  | 'reauth_required'
  | 'signed_out'
  | 'sidecar_auth_bridge_failed'
```

Tests must cover valid backup hydration through `supabase.auth.setSession()`, expired backup rejection, terminal refresh failure, genuine sign-out, and renderer-remote/sidecar-blind mismatch.

- [ ] **Step 2: Run RED packaged-lifecycle tests**

Run: `npm test -- tests/unit/restore-auth-backup.test.ts tests/unit/local-api/auth-availability.test.ts tests/unit/electron/local-api-lifecycle.test.ts`
Expected: any missing state or session replay path fails.

- [ ] **Step 3: Make session delivery acknowledged and self-healing**

Electron main must acknowledge the exact user ID/session generation delivered to the sidecar. Renderer recovery retries only while its Supabase client remains remotely valid; it never replays an expired backup or clears a still-valid sidecar during transient initialization.

- [ ] **Step 4: Add freshly packaged and installed cold-restart proof**

The diagnostic must require health plus protected inventory and exact-task reads. `health=true` with a protected 503 is `sidecar_auth_bridge_failed`, never healthy.

- [ ] **Step 5: Commit**

Commit: `fix(auth): recover protected assistant reads after cold restart`

### Task H2: Make complete inventory the only exhaustive task boundary

**Files:**
- Modify: FlowState `server/local-api/task-inventory.cjs`
- Modify: FlowState `server/local-api/task-search.cjs`
- Modify: FlowState `server/local-api/server.cjs`
- Modify: Hermes `tools/flowstate_tool.py`
- Modify: Hermes `agent/personal_assistant_monitor.py`
- Test: FlowState `tests/unit/local-api/task-inventory.test.ts`
- Test: Hermes `tests/tools/test_flowstate_tool.py`
- Test: Hermes `tests/agent/test_personal_assistant_monitor.py`

- [ ] **Step 1: Fail on sample/exhaustive ambiguity**

Filtered list and search responses must include:

```json
{"complete": false, "scope": "filtered_sample", "limit": 25, "hasMore": true}
```

Only inventory may emit `complete: true`, `fresh: true`, an exact `total`, a stable `scopeFingerprint`, and matching before/after canonical change sequences.

- [ ] **Step 2: Add >100-row, page-loss, concurrent-change, and scope-switch tests**

Run: `npm test -- tests/unit/local-api/task-inventory.test.ts`
Expected: RED until later-page loss and mid-read sequence changes fail closed without an exact total.

- [ ] **Step 3: Replace monitor `/api/tasks?limit=25` reads with validated inventory**

The monitor must emit connector failure rather than compare a partial sample. Its normalized snapshot stores IDs, revisions, sequence, scope fingerprint, and only the bounded fields used by candidate rules.

- [ ] **Step 4: Verify Hermes rejects stale or incomplete inventory**

Run: `python -m pytest -q tests/tools/test_flowstate_tool.py tests/agent/test_personal_assistant_monitor.py`
Expected: exact counts appear only for complete fresh inventory.

- [ ] **Step 5: Commit**

Commit: `fix(monitor): consume complete canonical task inventory`

### Task H3: Use one canonical receipt validator at every boundary

**Files:**
- Create: FlowState `server/local-api/canonical-receipt.cjs`
- Create: FlowState `tests/unit/local-api/canonical-receipt.test.ts`
- Modify: FlowState `server/local-api/canonical-task-patch.cjs`
- Modify: FlowState `server/local-api/done-for-now.cjs`
- Modify: FlowState `server/local-api/merge-tasks.cjs`
- Create: Hermes `tools/flowstate_receipts.py`
- Create: Hermes `tests/tools/test_flowstate_receipts.py`

- [x] **Step 1: Define and test the exact receipt contract**

```ts
type CanonicalReceipt<T> = {
  ok: true
  status: 'committed' | 'replayed'
  operationId: string
  requestHash: string
  canonicalRevision: number
  changeSequence: number
  committedAt: string
  readBack: T
  readBackHash: string
}
```

Tests recompute SHA-256 over canonical JSON. A well-shaped but incorrect hash, mismatched operation, missing revision/sequence, altered replay, or HTTP-only `ok: true` must be rejected.

- [x] **Step 2: Run RED receipt tests**

Run: `npm test -- tests/unit/local-api/canonical-receipt.test.ts && python -m pytest -q tests/tools/test_flowstate_receipts.py`
Expected: malformed successes currently pass and make the tests fail.

- [x] **Step 3: Route patch, done-for-now, and merge through the validator**

Domain receipts may retain operation-specific fields, but committed success must link to the canonical operation ledger and exact affected task revisions/sequences/read-backs.

- [x] **Step 4: Verify response-loss replay**

Apply once, discard the response, retry the same operation/payload, and assert `status: "replayed"`, one revision increment, one sequence event, and identical read-back hash.

- [x] **Step 5: Commit**

Commit: `feat(local-api): validate one canonical assistant receipt`

### Task H3a: Expose canonical non-recurring completion to Hermes

**Files:**
- Modify: Hermes `tools/flowstate_tool.py`
- Modify: Hermes `toolsets.py`
- Modify: Hermes `agent/tool_guardrails.py`
- Modify: office-work profile skill `skills/productivity/flowstate-personal-assistant-triage/SKILL.md`
- Test: Hermes `tests/tools/test_flowstate_tool.py`
- Test: Hermes `tests/agent/test_tool_guardrails.py`
- Test: Hermes `tests/run_agent/test_tool_call_guardrail_runtime.py`

- [x] **Step 1: Lock preview, apply, replay, typed conflict, and forged-receipt behavior with RED tests**
- [x] **Step 2: Register `flowstate_complete_task` against the signed-user `/api/tasks/:id/complete` boundary**
- [x] **Step 3: Require exact preview binding and verify completed status, timestamp, revision, sequence, and read-back hash**
- [x] **Step 4: Halt the current mutation batch when a recurring task requires a fresh `Done for now` preview**
- [x] **Step 5: Update the active office-work skill so triage evidence is not mistaken for completion approval**

Source contract verified 2026-07-15 with 272 related pytest regressions. Packaged gateway activation and protected live read-back remain part of H11 and are intentionally held until FlowState authentication is restored.

### Task H4: Canonicalize task create, delete, restore, and status transitions

**Files:**
- Create: FlowState `supabase/migrations/20260715040000_canonical_task_lifecycle.sql`
- Create: FlowState `server/local-api/task-lifecycle.cjs`
- Modify: FlowState `server/local-api/server.cjs`
- Modify: Hermes `tools/flowstate_tool.py`
- Test: FlowState `scripts/db/test-task-lifecycle-rpc.sql`
- Modify: FlowState `scripts/db/test-reliable-assistant-contract.sh`
- Test: FlowState `tests/unit/local-api/task-lifecycle.test.ts`
- Test: Hermes `tests/tools/test_flowstate_tool.py`

- [ ] **Step 1: Write rollback-only create/delete/restore tests**

Cover preview zero-write, exact workspace scope, deterministic generated task ID, stale revision, altered replay, cross-user/viewer denial, soft-delete tombstone, restore conflict against the live recurrence indexes, response loss, explicit non-recurring reopen, recurring reopen rejection, and injected rollback.

- [ ] **Step 2: Implement one `task.lifecycle.v1` operation family**

Create returns the previewed ID; delete, restore, and reopen require the current canonical revision. Completion remains a separate domain command: non-recurring tasks use `flowstate_complete_task`, recurring tasks use `flowstate_done_for_now`, and generic scalar patch never changes status.

- [ ] **Step 3: Make Hermes preview by default and halt on typed conflicts**

No create/delete/restore/reopen tool may report success from HTTP status alone or issue a fallback patch. The old bare DELETE route must fail closed rather than retaining a weaker mutation path.

- [ ] **Step 4: Run the full lifecycle contract and commit**

Run: `npm test -- tests/unit/local-api/task-lifecycle.test.ts && bash scripts/db/test-reliable-assistant-contract.sh && python -m pytest -q tests/tools/test_flowstate_tool.py`

Commit: `feat(tasks): add canonical lifecycle commands`

### Task H5: Canonicalize subtasks and interactive task breakdown

**Files:**
- Create: FlowState `supabase/migrations/20260715021000_canonical_subtask_batch.sql`
- Create: FlowState `server/local-api/subtask-batch.cjs`
- Modify: FlowState `server/local-api/server.cjs`
- Modify: Hermes `tools/flowstate_tool.py`
- Test: FlowState `scripts/db/test-subtask-batch-rpc.sql`
- Test: FlowState `tests/unit/local-api/subtask-batch.test.ts`
- Test: Hermes `tests/tools/test_flowstate_tool.py`

- [ ] **Step 1: Replace process-local receipt tests with durable restart tests**

Preview A/apply altered B rejects; same request/different payload conflicts before and after sidecar restart; concurrent app and Hermes edits preserve both or return `stale_revision`; injected failure rolls back the entire approved batch.

- [ ] **Step 2: Bind the approved ordered breakdown**

The preview contains parent ID/revision and exact ordered operations:

```json
{"operations":[{"kind":"create","clientId":"step-1","title":"Draft outline","doneEnough":"Outline has five headings","estimateMinutes":20}]}
```

Apply returns canonical parent revision/sequence plus exact normalized subtask read-back.

- [ ] **Step 3: Preserve partial completion semantics**

Subtasks may be intentionally “done enough”; no rule requires every breakdown step or parent task to reach maximal implementation.

- [ ] **Step 4: Verify and commit**

Commit: `feat(subtasks): make assistant breakdown atomic and replayable`

### Task H6: Canonicalize work-block create, move, resize, and remove

**Files:**
- Create: FlowState `supabase/migrations/20260715022000_canonical_work_blocks.sql`
- Create: FlowState `server/local-api/work-blocks.cjs`
- Modify: FlowState `server/local-api/server.cjs`
- Modify: Hermes `tools/flowstate_tool.py`
- Test: FlowState `scripts/db/test-work-block-rpc.sql`
- Test: FlowState `tests/unit/local-api/work-blocks.test.ts`
- Test: Hermes `tests/tools/test_flowstate_tool.py`

- [ ] **Step 1: Prove the current random-ID approval bug**

The RED test asserts preview and apply use the same stable work-block ID, identical retry creates no duplicate, changed payload conflicts, and concurrent sibling appends are preserved.

- [ ] **Step 2: Implement exact interval commands**

Preview shows before/after local time, timezone, duration, overlap warnings, task/due-date effects, and finish-by boundary. Apply uses operation identity, task revision, and work-block revision.

- [ ] **Step 3: Add renderer mutation notification and authoritative read-back**

Calendar, Today, Search, Inbox, and Canvas reload the exact affected task/work-block IDs; notification failure cannot erase durable success.

- [ ] **Step 4: Verify and commit**

Commit: `feat(schedule): add canonical work-block lifecycle`

### Task H7: Complete recurrence, merge, timer, and organization commands

**Files:**
- Modify: FlowState `server/local-api/done-for-now.cjs`
- Modify: FlowState `server/local-api/merge-tasks.cjs`
- Create: FlowState `server/local-api/recurrence-lifecycle.cjs`
- Create: FlowState `server/local-api/timer-commands.cjs`
- Create: FlowState `server/local-api/organization.cjs`
- Modify: FlowState `server/local-api/server.cjs`
- Modify: Hermes `tools/flowstate_tool.py`
- Test: FlowState `tests/unit/local-api/recurrence-lifecycle.test.ts`
- Test: FlowState `tests/unit/local-api/timer-commands.test.ts`
- Test: FlowState `tests/unit/local-api/organization.test.ts`

- [ ] **Step 1: Preserve established recurrence history as fail-closed**

Cadence edit/pause/resume/end-series may change future definition only; no command erases completed occurrences or guesses an ambiguous current occurrence. Merge retains the new hard-stop action for incompatible definitions/history.

- [ ] **Step 2: Add protected timer state-machine commands**

Start/pause/resume/stop require bearer auth, stable session/operation IDs, exact transition preview, leadership checks, replay protection, and canonical read-back. The unauthenticated KDE read endpoint is not reused for writes.

- [ ] **Step 3: Add project/group/lane and Canvas capability primitives**

Reads return exact accessible IDs/names/scope. Assignment, group membership, lane placement, and Canvas move/group/ungroup/remove are distinct previewed operations; removing placement never deletes the task.

- [ ] **Step 4: Add a capability manifest**

`GET /api/capabilities` and a Hermes registry test must enumerate every supported read/write, approval mode, receipt version, scope mode, and typed unsupported reason. This prevents the model from inventing fallback operations.

- [ ] **Step 5: Verify each cohort and commit separately**

Commits:
- `feat(recurrence): add future-series lifecycle commands`
- `feat(timer): expose protected canonical timer transitions`
- `feat(organization): expose scoped project group lane and canvas commands`

### Task H8: Make monitor events complete, causal, and non-interrupting

**Files:**
- Modify: Hermes `agent/personal_assistant_monitor.py`
- Modify: Hermes `agent/personal_assistant_state.py`
- Modify: Hermes `tui_gateway/server.py`
- Modify: Hermes `agent/personal_assistant_service.py`
- Test: Hermes `tests/agent/test_personal_assistant_monitor.py`
- Test: Hermes `tests/tui_gateway/test_personal_assistant_monitor_consumer.py`

- [ ] **Step 1: Add RED causal-suppression tests**

Hermes-authored create/update/merge must not launch a new episode; an external consequential mutation must. Busy planning turns defer/coalesce without reset. Connector refusal exits nonzero. A lease restarted mid-delivery settles exactly once.

- [ ] **Step 2: Store structured canonical evidence**

```json
{
  "schemaVersion": 2,
  "eventId": "stable logical mutation id",
  "changeSequence": 123,
  "operationId": "uuid-or-null",
  "origin": "hermes|web|pwa|electron|unknown",
  "taskIds": ["exact-id"],
  "scopeFingerprint": "sha256",
  "lifecycle": "candidate|suppressed|merged|leased|handled|failed|dead_letter"
}
```

The model receives rendered text only at the final prompt boundary; canonical evidence remains structured internally.

- [ ] **Step 3: Remove duplicate busy-turn merge and enforce visible completion**

Delivery is not handling. Settle only after the episode is visible and complete, or record retry/dead-letter with a user recovery action.

- [ ] **Step 4: Arm consumer-stale alerts only when a consumer owner exists**

The personal-assistant consumer currently lives inside the owning office-work Desktop/TUI gateway. The watchdog must not report it stale when that owner is intentionally absent. When an owner is alive, heartbeat expiry remains actionable. Active incidents are keyed by component/reason and retain resolved state so a low-value stale alert cannot overwrite a connector failure.

- [ ] **Step 5: Verify and commit**

Run: `python -m pytest -q tests/agent/test_personal_assistant_monitor.py tests/tui_gateway/test_personal_assistant_monitor_consumer.py`

Commit: `fix(assistant): make monitor delivery complete and causal`

### Task H9: Expose a writable, user-directed Notion bridge

**Files:**
- Create: Hermes `tools/notion_task_tool.py`
- Create: Hermes `tests/tools/test_notion_task_tool.py`
- Modify: Hermes `model_tools.py`
- Modify: FlowState `server/local-api/notion-activation.cjs`
- Test: FlowState `tests/unit/local-api/notion-activation.test.ts`

- [ ] **Step 1: Separate Notion mutation from FlowState activation**

Notion remains source of truth for Bina project tasks. Reads, create, property/status updates, and archive are Notion commands. FlowState activation is a separate explicit preview/apply command with stable page provenance.

- [ ] **Step 2: Define exact writable operations**

```python
NOTION_ACTIONS = {"create_task", "update_properties", "set_status", "archive_task"}
```

Every preview binds database ID, page ID when present, exact property schema/types, normalized changes, expected `last_edited_time`, request ID, and expiry. Apply verifies Notion read-back and records a durable redacted receipt; schema drift, altered replay, response loss, and optimistic-version conflicts are typed.

- [ ] **Step 3: Expose existing canonical activation to Hermes**

Register preview/apply activation with strict receipt hash validation. It may create at most one active FlowState task per user/source/page and may add only the exact approved personal work block.

- [ ] **Step 4: Verify with disposable pages only and commit**

Run: `python -m pytest -q tests/tools/test_notion_task_tool.py && npm test -- tests/unit/local-api/notion-activation.test.ts`

Commit: `feat(notion): add verified user-directed task writes`

### Task H10: Make dynamic breakdown and planning an interactive Hermes contract

**Files:**
- Modify: Hermes `apps/desktop/src/lib/hermes-ui-artifacts.ts`
- Modify: Hermes `apps/desktop/src/components/assistant-ui/embeds/hermes-ui-artifact.tsx`
- Create: Hermes `apps/desktop/src/components/assistant-ui/embeds/task-breakdown-card.tsx`
- Create: Hermes `tools/flowstate_planning_artifacts.py`
- Modify: office-work profile skill `skills/productivity/flowstate-personal-assistant-triage/SKILL.md`
- Test: Hermes `apps/desktop/src/components/assistant-ui/embeds/hermes-ui-artifact.test.tsx`
- Test: Hermes `apps/desktop/src/app/session/hooks/use-prompt-actions/index.test.tsx`
- Test: Hermes `tests/tools/test_flowstate_planning_artifacts.py`
- Create: Hermes `tests/run_agent/test_model_iteration_limit.py`

- [ ] **Step 1: Add a strict `task-breakdown` artifact**

```ts
type TaskBreakdownArtifact = {
  type: 'task-breakdown'
  taskId: string
  taskRevision: number
  outcome: string
  finishBy?: string
  depth: 'light' | 'normal' | 'deep'
  steps: Array<{
    clientId: string
    title: string
    doneEnough: string
    estimateMinutes?: number
    required: boolean
    dependsOn: string[]
  }>
  unresolvedQuestion?: {id: string; prompt: string; options?: string[]}
}
```

Incomplete streaming JSON shows a preparation state, never raw code. RTL and English inputs render correctly. Text entry uses actual text fields; radio/checkbox primitives are used only for bounded choices.

- [ ] **Step 2: Ask only the first consequential unknown**

Before ordering a day, Hermes must establish finish-by time, fixed commitments, location/travel, task completion state, meaningful outcome, and duration uncertainty only when each changes the plan. It must not run a fixed morning script.

- [ ] **Step 3: Learn duration corrections contextually**

Store evidence as context dimensions—task kind, location/travel, scope/depth, tool/setup, and observed/corrected duration—with confidence. One explicit correction may override the current plan; it becomes a durable default only when the user states it is general or repeated compatible evidence supports it.

- [ ] **Step 4: Submit an exact H5/H6 preview, not prose**

Editing/reordering steps or times submits structured IDs and values to Hermes. Hermes renders the exact canonical preview and applies only after explicit approval.

- [ ] **Step 5: Acknowledge every interactive submission**

Artifact submit carries artifact, session, request, and turn IDs. Gateway returns `accepted`, `queued`, or `rejected`; Desktop retains the draft on failure, prevents duplicate clicks, and can retry the same request identity after reconnect or session resume.

- [ ] **Step 6: Replace silent model-iteration exhaustion with a checkpoint**

Read → clarify → preview → apply → read-back flows may exceed a fixed iteration cap. The assistant either receives a task-aware bounded extension or renders a visible resumable checkpoint naming completed and pending phases; it never stops on an unexplained spinner.

- [ ] **Step 7: Verify and commit**

Run: `npm --prefix apps/desktop test -- hermes-ui-artifact && python -m pytest -q tests/tools/test_flowstate_planning_artifacts.py`

Commit: `feat(assistant): add dynamic interactive task breakdown`

### Task H11: Enforce clean packaged runtime and continuous recovery proof

**Files:**
- Modify: FlowState `scripts/deploy-electron-update.sh`
- Modify: FlowState `scripts/validate-electron-package.cjs`
- Modify: Hermes `scripts/hermes_live_watchdog.py`
- Modify: Hermes `agent/tool_executor.py`
- Modify: Hermes `run_agent.py`
- Modify: Hermes `scripts/install-hermes-live-watchdog-service.sh`
- Create: Hermes `scripts/verify_hermes_flowstate_live.py`
- Test: Hermes `tests/test_hermes_live_watchdog.py`
- Create: Hermes `tests/agent/test_tool_executor_timeout.py`
- Test: Hermes `tests/run_agent/test_tool_call_guardrail_runtime.py`
- Test: Hermes `tests/test_verify_hermes_flowstate_live.py`

- [ ] **Step 1: Add runtime failure classifications**

The watchdog distinguishes `app_absent`, `defunct_process`, `main_without_renderer`, `renderer_without_sidecar`, `sidecar_health_only`, `reauth_required`, `protected_read_failed`, `stale_package`, and `healthy`. It never loops relaunches while a live unhealthy main process exists; it records a bounded manual recovery action.

Launch evidence records only redacted executable identity, child PID, start ticks, exit code/signal, renderer presence, port-bind timeline, and health/protected-read outcomes. It does not discard stdout/stderr before extracting a stable failure class.

- [ ] **Step 2: Add cancellable per-tool deadlines and heartbeats**

Sequential and concurrent tool calls use named policies. Long healthy calls emit heartbeats; a wedged call is interrupted only after owner session, turn identity, wait state, and lack of progress agree. Exactly one terminal result is delivered, and the next user send must work without restarting the conversation.

- [ ] **Step 3: Build only from a clean merged commit**

Package stamps must include exact commit and `dirty: false`. FlowState AppImage/deb validation, Hermes ASAR/install stamp, Local API bundle symbols, and native dependencies are release gates.

- [ ] **Step 4: Publish and install exact public bytes**

Verify manifest version, artifact reachability, sha512, installed sha512, executable mount, and live process executable. Preserve the previous artifact as rollback; never touch user task/profile/session data during package replacement.

- [ ] **Step 5: Run the real signed-in proof sequence**

Required order:

```text
cold restart -> renderer remotely authenticated -> sidecar health
-> protected complete inventory -> protected exact read
-> preview disposable/synthetic mutation -> approved apply
-> canonical read-back in FlowState -> Hermes receipt validation
-> monitor sees external change but suppresses Hermes-authored change
```

Production user tasks are not mutation fixtures. Use disposable signed-user fixtures with cleanup or rollback-only database proof unless the user approves an exact bounded mutation.

- [ ] **Step 6: Run full gates, update MASTER_PLAN in all parser locations, commit, and push**

Run FlowState lint/typecheck/full tests/database contracts/Electron build and Hermes lint/typecheck/focused/full desktop tests. Inspect final diffs, preserve unrelated work, use Lore trailers, push only fresh compatible branches, then record the H0 ledger.

Commit: `chore(release): prove Hermes FlowState reliability lane`

## Self-review result

- Coverage includes auth, inventory, every current mutation cohort, recurrence/history, work blocks, subtasks, organization, timers, Canvas, offline/Realtime authority, monitor causality, writable Notion, interactive decomposition, duration learning, packaging, watchdogs, and live proof.
- No operation may fall back to a weaker write after a typed conflict.
- No sample may be presented as a complete inventory.
- No delivery event is treated as handled before visible completion.
- No production mutation or credential exposure is required by the test plan.
- The implementation sequence produces independently testable software after every task and preserves the existing parent TASK-1943 program lane.
