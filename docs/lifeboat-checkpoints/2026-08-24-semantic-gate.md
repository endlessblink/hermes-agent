# Life-Boat checkpoint — semantic continuity gate

Date: 2026-08-24
Branch: `main`
Repository: `lifeboat-live`

## Return point

Resume from this checkpoint by first reading this file, `HANDOFF.md`, the
current `AGENTS.md`, and the skill at `skills/lifeboat-editor-grounding/SKILL.md`.
Do not assume older replay claims are valid. The acceptance surface is the
reply delivered by the live Telegram bot.

The current objective is global: improve replies across support conversations,
not one debrief sentence or one named scenario. A fix is not accepted because
unit tests pass, a prompt sounds good, or a source file changed.

## Current verified state

- Current HEAD: `2bcf38dce0` — Hebrew for how it sounds, English for what it looks for.
- The latest three commits add a global purpose/voice layer and make the voice
  guidance Hebrew-aware; they are not a hardcoded reply for the debrief example.
- The live gateway is active as PID `2432027`.
- Source and installed hashes match for the current Life-Boat gateway modules,
  including the voice layer and psychology guidance.
- Startup logged a passed runtime check at `02:49:40`.
- The editor is deliberately OFF: `~/.hermes/lifeboat-editor-off` exists.
- The selected voice is `friend` via `~/.hermes/lifeboat-voice`.
- The current focused suite passes: `212 passed`.
- The only available six-turn replay aggregate is stale: it ran before the
  latest voice commits, and it did not prove improved delivered language.
- Its aggregate was: no-change 6 turns, coach-cleaned 6, close-friend 6; most
  editor rewrites were rejected. This is diagnostic evidence, not a quality win.
- Dirty `baselines/lifeboat/**` files belong to the parallel work and must be
  preserved. Do not reset, overwrite, or fold them in without reviewing them.
- The goal remains unfinished. No global quality improvement is proven yet.

## Problem definition

Observed failures are broader than repeated literal wording:

1. The bot asks for information already present in the recent conversation.
2. It invents a desire, goal, topic, time, or subject area for the user.
3. It stops at interpretation instead of carrying the conversation forward.
4. It hands control back to the user by repeatedly asking them to choose a
   starting point or supply direction.
5. A rewrite can fail while the delivery path still risks sending a rejected
   draft. No rejected output may reach Telegram.
6. English instructions can produce an unwanted Hebrew therapeutic register;
   Hebrew instruction text can be copied verbatim as a canned reply.

Additional failing surfaces now in scope:

7. Morning check-ins do not reliably start from the user's actual current
   situation and can feel like a generic routine.
8. Casual checkups do not reliably respond to what is happening now and can
   turn into an intake question or a request for the user to steer.
9. Evening summaries do not reliably summarize the actual day and can invent
   events, flatten the day into advice, or ask the user to reconstruct the
   summary instead of helping make it.

The full Hebrew conversation supplied on 2026-08-24 is a regression fixture,
not a special-case rule. Names, exact questions, and exact answers from it must
not become production conditions.

## Target architecture

Keep the existing draft → review → optional rewrite → delivery path, but add a
semantic continuity gate after the deterministic checks and before delivery.

### Trusted input

The gate receives:

- the current user message;
- the last relevant user and assistant turns, with thread/session identity;
- a provenance-safe working state containing only user-originated evidence;
- the current draft;
- an optional factual state summary: what was said, what remains unknown, and
  the last concrete event.

The raw recent turns remain available to audit the summary. Assistant
hypotheses, suggestions, prior drafts, queue items, dev notes, and cross-thread
material are never trusted facts. A summary must not be allowed to launder an
assistant inference into user evidence.

### Structured semantic verdict

Use a separate semantic checker that returns JSON only, never reply prose. The
verdict should contain independent flags rather than one mutually exclusive
label, because a reply can fail in more than one way:

```json
{
  "pass": true,
  "repeated_request": false,
  "invented_user_goal": false,
  "responsibility_handoff": false,
  "concrete_continuation": true,
  "evidence_turn_ids": [],
  "reason": "short audit reason"
}
```

The four useful concepts are:

- repeated request: asks for information already supplied, semantically, not
  merely by matching words;
- invented user goal: assigns a desire, intention, topic, or objective the user
  did not state;
- responsibility handoff: asks the user to manage the conversation when the
  assistant could choose a reasonable next step;
- concrete continuation: responds to the latest event and advances it, either
  with a cautious interpretation offered for correction or with a concrete next
  step chosen by the assistant.

These are not bans on questions. A question passes when it seeks genuinely
missing information, follows the latest event, and does not make the user
choose the entire direction of the conversation.

### Failure path

1. Run existing deterministic checks.
2. Run the semantic checker on the relevant recent sequence and trusted state.
3. If all gates pass, deliver the draft unchanged unless a targeted quality
   edit is independently judged better.
4. If a gate fails, call the editor once with the structured failure reasons.
5. Re-run all gates on the edited result.
6. Deliver only a passing result.
7. If the original draft failed and the rewrite also fails, never deliver the
   rejected original. Retry through an explicitly bounded generation path or
   fail closed with a separately generated response that must itself pass the
   same gate. No reviewer-authored canned fallback is allowed.

The editor and semantic checker must not write user-facing prose. The main
model remains responsible for the answer; reviewers decide whether it is safe
and useful to deliver.

## Implementation order

### Phase 1 — lock the contract

- Define the structured verdict schema and parser.
- Define typed state/provenance boundaries.
- Add fixtures from the supplied Hebrew conversation as regression data only.
- Add positive cases where a question is necessary and should pass.
- Add cases where one reply has multiple failure flags.

Gate: deterministic tests prove no assistant suggestion becomes a user fact.

### Phase 2 — semantic checker in shadow mode

- Send the same draft, recent sequence, and state to the checker.
- Log verdicts, evidence turn IDs, latency, and disagreement with the current
  reviewer, but do not change delivery.
- Run on varied conversations: debrief, ordinary support, correction, low
  energy, self-criticism, proactive updates, morning check-in, casual checkup,
  evening summary, mixed Hebrew/RTL, and safety turns.

Gate: inspect disagreement samples; no rollout claim from scores alone.

### Phase 3 — repair and fail-closed delivery

- Add one rewrite attempt using structured reasons.
- Re-check the rewrite.
- Make the delivery invariant explicit: a rejected original cannot be sent.
- Preserve a passing original when only an attempted edit fails.
- Add rollback and a kill switch.

Gate: unit and integration tests cover original-pass/edit-fail, original-fail/
edit-pass, and original-fail/edit-fail.

### Phase 4 — real delivered evaluation

- Restart the gateway after every runtime change.
- Verify PID, active timestamp, source/installed hashes, editor flag, model,
  reasoning effort, and startup receipt.
- Run editor-off control and editor-on arms over a broad scenario matrix.
- Read the actual delivered Telegram replies, not only JSON decisions.

Gate: editor-on reduces repeated requests, invented goals, handoffs, and
therapeutic register without increasing suppression, false revisions, or
conversation stalls.

### Phase 5 — hardening after quality is demonstrated

Only after the delivered matrix improves:

- add claim-level support scoring where useful;
- calibrate thresholds using audited labels and cost-sensitive error weights;
- add drift monitoring and a safe threshold fallback;
- add static and bounded adaptive red-team fixtures;
- require every discovered failure to become a regression fixture.

Do not add NLI, calibration, drift, or adaptive attack machinery merely because
it is theoretically useful. Each addition needs a measured benefit on the
actual delivered surface.

## Evaluation contract

For every scenario, record:

- draft and delivered reply separately;
- runtime receipt/hash and editor state;
- reviewer verdicts and rewrite outcome;
- groundedness and no-laundering;
- repeated-request rate;
- invented-goal rate;
- responsibility-handoff rate;
- concrete-continuation rate;
- morning check-in usefulness and non-genericness;
- casual checkup relevance and conversational naturalness;
- evening-summary factual coverage, omission, and invention rates;
- warm ordinary tone;
- false-revision and suppression rate;
- human preference between editor-off and editor-on where possible.

The minimum release bar is:

- current runtime receipt matches the deployed source;
- deterministic and integration tests pass;
- safety regression cases do not pass the gate;
- positive question cases still pass;
- editor-on wins or clearly improves the broad delivered matrix without a
  material regression in warmth, engagement, or continuation;
- a real Telegram readback has been inspected.

If any one of these is missing, status remains `in_progress`.

## Rollback

- Keep the editor disabled while shadow results are inconclusive.
- If live quality regresses, restore the last receipt-verified runtime and
  restart the gateway.
- Never deploy from a dirty worktree containing another instance's changes.
- Never reset or overwrite the dirty baseline snapshots.

## Optional Perplexity research

Use this only if implementation encounters an unanswered design question:

> For production multi-turn emotional-support chat, find primary research and
> authoritative engineering sources on semantic conversation-state tracking and
> response evaluation gates that detect repeated questions, invented user goals,
> responsibility handoff, and failure to advance the latest event. Focus on
> structured evaluator outputs, provenance-safe summaries, claim/state
> grounding, abstention or fail-closed delivery, and shadow-mode rollout.
> Compare single LLM judges, independent second judges, NLI/entailment checks,
> and hybrid deterministic-plus-semantic gates. Require evidence from 2023–2026,
> identify what has been evaluated on real multi-turn conversations, distinguish
> research findings from engineering inference, and report concrete metrics and
> failure modes. Do not recommend canned phrase lists or exact-sentence
> hardcoding.

## First action on return

Do not enable the editor yet. Review the current committed voice changes and
dirty baseline snapshots, implement the structured semantic verdict in shadow
mode, and run the broad delivered matrix before changing live delivery.
