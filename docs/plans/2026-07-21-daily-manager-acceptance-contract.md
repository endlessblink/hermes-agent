# Daily Manager Acceptance Contract

Hermes passes only when the packaged Desktop can manage a real planning cycle without relying on task names, fixture-specific rules, or an unverified subset of the user's work.

## Candidate completeness

- Discover every configured task source at runtime; do not rely on the model to remember a source.
- The office-work profile owns the source manifest under `personal_assistant.task_sources`; each entry has an arbitrary stable `id` and `inventory_tool`. FlowState remains the backward-compatible default when the key is absent.
- Read every page from each available source and retain stable source and task identifiers.
- Keep timed, untimed, blocked, deferred, recurring, low-visibility, and no-due-date work in the considered universe.
- Produce a coverage receipt containing every expected source, its freshness/completeness status, every considered task ID, and every excluded task ID with a reason.
- If a source is unavailable, stale, partial, or unchecked, say so and label the plan degraded. Never claim all tasks were considered.

## Generic recommendations

- Recommendations must change when arbitrary unknown tasks are added, removed, or edited.
- Renaming task fixtures must not change eligibility except through the renamed content itself.
- Ranking happens only after complete candidate generation; no ranker may hide incomplete retrieval.
- Recent recommendations and outcomes are retained so Hermes avoids repetitive suggestions without suppressing urgent work.

## Durable user updates

- An explicit correction creates a visible, provenance-preserving proposed update.
- After exact approval, the update is applied once, read back from its durable authority, and affects the next planning cycle after restart.
- Conflicting updates remain visible and unresolved; Hermes never silently chooses one.
- Acknowledging a correction without persisting and using it is a failure.

## Recovery

- A question, repair prompt, partial result, or safe fallback never marks daily planning complete.
- A valid plan closes the daily run only after a new complete daily coverage receipt from that run.
- Source failures preserve already-read evidence and pending user decisions, offer bounded retry/repair/cancel actions, and resume from the failed boundary.
- Repeated identical failures stop looping, remain visible, and keep the planning run retryable.

## Approval and execution

- Planning and task changes remain previews until the user approves the exact outcome.
- Nothing external changes before approval.
- Each approved change executes once and is independently read back; partial success is reported precisely.

## Required proof

1. Use generated task IDs and titles across every configured source, including more items than one page.
2. Prove the coverage receipt accounts for every candidate and every exclusion.
3. Correct one recommendation rule, approve it, restart Hermes, and prove the next ranking changes.
4. Fail one source mid-read, recover it, and prove the same planning cycle completes without losing prior evidence or decisions.
5. Prove zero mutations before approval and exactly one mutation after approval.
6. Repeat the flow in the installed packaged Desktop against the signed-in user surface before claiming the daily manager works.
