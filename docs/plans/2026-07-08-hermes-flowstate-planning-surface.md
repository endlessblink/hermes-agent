# Hermes FlowState Planning Surface Implementation Plan

> **For Codex/Hermes developers:** Build the durable Hermes-side planning and tracking surface. FlowState remains the task-management source of truth. Hermes Desktop is the planning, monitoring, triage, and visual decision surface.

**Goal:** Render FlowState planning inside Hermes Desktop as interactive visual cards and dashboards, not as plain text summaries and not inside the FlowState app.

**Architecture:** FlowState provides state and mutations through its Local Task API. Hermes reads FlowState, reasons over overload/priority, then emits safe `hermes-ui` artifacts rendered inline by Hermes Desktop. User decisions in those artifacts submit structured follow-up requests back to Hermes, which previews FlowState writes and applies them only after explicit approval via existing FlowState tools/API.

**Tech Stack:** Hermes Desktop React/TypeScript, existing `hermes-ui` fenced artifacts, FlowState toolset, Python `tools/flowstate_tool.py`, Hermes session loop, Vitest/desktop tests, pytest for tool behavior.

---

## Product principle

The user’s overload is the problem. A text answer that lists tasks is a failure mode. The default planning interaction must be a rendered Hermes UI surface:

- category cards first;
- next-block card;
- at most 1 to 3 task recommendation cards visible by default;
- explicit controls for “today”, “not today”, “defer”, “discuss”, due date, priority, and clearly labeled completion;
- one submit/apply-request action that routes choices back into Hermes;
- Hermes then shows a preview before any FlowState write;
- FlowState remains the management backend and source of truth.

## Existing Hermes foundation

Hermes Desktop already has `hermes-ui` artifact infrastructure:

- `apps/desktop/src/lib/hermes-ui-artifacts.ts`
- `apps/desktop/src/components/assistant-ui/embeds/hermes-ui-artifact.tsx`

Existing artifact types include:

- `checklist`
- `task-triage`
- `flowstate-task-batch`

This plan extends that direction into a real planning surface and makes Hermes use it consistently.

## Task 1: Add a durable planning artifact schema

**Objective:** Add an explicit artifact type for Hermes planning, separate from raw FlowState task lists.

**Files:**
- Modify: `apps/desktop/src/lib/hermes-ui-artifacts.ts`
- Modify: `apps/desktop/src/components/assistant-ui/embeds/hermes-ui-artifact.tsx`
- Test: `apps/desktop/src/lib/hermes-ui-artifacts.test.ts`
- Test: `apps/desktop/src/components/assistant-ui/embeds/hermes-ui-artifact.test.tsx`

**New artifact type:** `flowstate-planning-session`

**Schema sketch:**

```ts
type HermesUiFlowStatePlanningSession = {
  type: 'flowstate-planning-session'
  direction?: 'auto' | 'rtl' | 'ltr'
  id?: string
  title?: string
  description?: string
  mode: 'day-start' | 'overload-relief' | 'end-of-day' | 'quick-triage'
  categories: Array<{
    id: string
    label: string
    tone: 'risk' | 'health' | 'pet' | 'work' | 'money' | 'life' | 'creative' | 'maintenance'
    count: number
    recommendation: string
    examples: Array<{
      id: string
      title: string
      dueDate?: string | null
      priority?: 'high' | 'medium' | 'low' | null
    }>
  }>
  nextBlock?: {
    id: string
    title: string
    durationMinutes: number
    taskIds: string[]
    doneEnough: string
    rationale: string
  }
  tasks: Array<{
    id: string
    title: string
    status?: string
    priority?: 'high' | 'medium' | 'low' | null
    dueDate?: string | null
    projectId?: string | null
    recommendation?: 'today' | 'not_today' | 'later' | 'discuss'
    recommendedPriority?: 'high' | 'medium' | 'low' | null
    recommendedDueDate?: string | null
    rationale?: string
  }>
}
```

**Validation rules:**
- Max categories: 5.
- Max examples per category: 2.
- Max visible tasks: 5, but renderer should visually collapse after 3.
- Text fields are text only. No HTML, Markdown execution, JavaScript, or dangerous rendering.
- Do not include raw tokens, auth headers, sessions, `.env` values, or hidden chain-of-thought.

## Task 2: Render the planning session as a Hermes Desktop visual surface

**Objective:** Render the artifact as a compact, RTL-safe planning dashboard inside the chat message.

**Files:**
- Modify: `apps/desktop/src/components/assistant-ui/embeds/hermes-ui-artifact.tsx`
- Optional create: `apps/desktop/src/components/assistant-ui/embeds/flowstate-planning-session-card.tsx`

**UX requirements:**
- Header with mode/title and short description.
- Category cards grid.
- Next block card if provided.
- Task recommendation cards limited by default.
- Controls per task:
  - today;
  - not today;
  - later;
  - discuss;
  - due date quick buttons;
  - priority selector;
  - completion checkbox clearly labeled as done/completed.
- One “שלח החלטות ל־Hermes” / “Send decisions to Hermes” action.
- Action uses `requestComposerSubmit(...)`, as the existing `flowstate-task-batch` renderer does.

## Task 3: Make FlowState planning replies default to artifacts, not prose

**Objective:** Change Hermes behavior/skill guidance so FlowState planning sessions produce `hermes-ui` artifacts by default.

**Files:**
- Modify office-work skill: `~/.hermes/profiles/office-work/skills/productivity/flowstate-personal-assistant-triage/SKILL.md`
- Potentially add reference/template file under that skill, for example `references/hermes-ui-flowstate-planning-session.md`

**Behavior:**
- When FlowState data is available, assistant should emit a `hermes-ui` planning artifact.
- Text should only be a one-line frame, not the main content.
- If artifact rendering is unavailable, say so explicitly and move to infrastructure work. Do not dump backlog text.

## Task 4: Add a Python helper to produce artifact JSON from FlowState tasks

**Objective:** Avoid handcrafting fragile JSON every time. Provide a reusable generator used by Hermes skills or future tools.

**Files:**
- Create: `tools/flowstate_planning_artifacts.py` or a plugin/helper if core footprint is too high.
- Test: `tests/tools/test_flowstate_planning_artifacts.py`

**Behavior:**
- Accept compact FlowState task rows.
- Categorize into health/pet/money/work/creative/maintenance/stale/no-date.
- Select at most 5 categories and at most 5 tasks.
- Return a valid `hermes-ui` JSON string, not rendered Markdown.
- No FlowState writes.

## Task 5: Add an apply-preview parser for submitted decisions

**Objective:** When the user clicks “Send decisions to Hermes”, Hermes should parse the structured decision text and produce a real FlowState preview.

**Files:**
- Prefer existing FlowState tool flow if sufficient.
- If adding code: `tools/flowstate_tool.py` or a service-gated helper module.
- Tests: `tests/tools/test_flowstate_tool.py` or a new focused test.

**Behavior:**
- Parse task IDs, decisions, proposed due dates/priorities, completion flags.
- Show preview before writes.
- Apply only after explicit user approval.
- Verify after apply by reading back FlowState tasks.

## Task 6: End-to-end desktop proof

**Objective:** Prove this is actually rendered in Hermes Desktop.

**Verification:**

```bash
cd /home/endlessblink/.hermes/hermes-agent
npm --prefix apps/desktop test -- hermes-ui-artifact
scripts/run_tests.sh tests/tools/test_flowstate_planning_artifacts.py
```

Then manually send a message containing a `flowstate-planning-session` `hermes-ui` fenced block and verify:

- Hermes Desktop renders cards, not a code block.
- RTL Hebrew cards align correctly.
- Buttons update local UI state.
- “Send decisions to Hermes” submits a structured message into the composer.
- No FlowState mutation occurs until a later explicit preview/apply step.

## Non-goals

- Do not build the planning UI inside FlowState for this slice.
- Do not add raw DB writes.
- Do not make FlowState the planning UI.
- Do not require MCP for the first version.
- Do not auto-apply schedule changes.
