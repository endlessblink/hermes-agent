---
name: lifeboat-editor-grounding
description: Use whenever editing, reviewing, replaying, deploying, or evaluating the Life-Boat emotional-support reply editor. Keep revisions grounded in explicit user evidence, prevent assistant-context laundering, preserve warm ordinary language, and prove the running gateway loaded the changed runtime before claiming a live result.
---

# Life-Boat editor grounding

Use this workflow for every change to the Life-Boat editor or its live delivery
path. The goal is useful forward motion without inventing a time, event, place,
feeling, or conversation fact.

## Evidence model

Represent evidence explicitly instead of flattening it into one prompt:

`EvidenceItem(id, text, provenance, epistemic_status, thread_id, source_turn_id, timestamp, superseded_by)`

`ConversationState(thread_id, user_facts, assistant_hypotheses, unresolved_suggestions, confirmed_corrections, historical_context, quarantined)`

Only current-thread explicit user facts and corrections enter trusted premises.
Historical items require relevance and an explicit historical label.

Keep these sources separate:

- `user_facts`: explicit user statements, each with its turn id.
- `assistant_hypotheses`: interpretations, suggestions, and proposed anchors.
- `confirmed_corrections`: explicit user corrections or clarifications.
- `unresolved_suggestions`: assistant proposals not explicitly confirmed.

Build `user_facts` and corrections only from user turns. A reply of “yes”,
“okay”, or “sure” confirms agreement with the immediately preceding exchange;
it does not confirm every factual detail inside an assistant proposal. Never
promote an assistant-generated time, event, or anchor into a user fact.

Affirmation-only turns contain no new concrete fact. Promote a suggestion only
when the user restates it or clearly confirms a specific action in their own
words; “yes” alone does not confirm every detail of an assistant proposal.

Treat every queue, journal, transcript, and memory source as untrusted until
its provenance is known. Quarantine operational notes, development threads,
engine bookkeeping, assistant summaries, and mixed-purpose logs; do not pass
them into the editor's user-premise set. An empty trusted evidence set is safer
than a fluent summary of contaminated material.

## Editing contract

1. Extract a compact list of atomic user-supported premises for the current
   reply; do not include assistant text as evidence.
2. Identify concrete claims in the draft and bind each to a user turn or mark
   it unsupported.
3. Preserve the draft when it is grounded, advances the exchange, and uses
   warm everyday language.
4. Otherwise revise only unsupported spans. A chosen next step is valid only
   when the action is grounded or the assistant clearly labels it as a proposed
   action without treating its anchor as history.
5. If evidence is insufficient, abstain from concrete claims and use a narrow,
   low-pressure continuation rather than inventing a time or event.

Progress means either a tentative read offered for correction or a concrete
assistant-chosen next action. It does not mean making the user choose the frame,
organize the facts, or supply a direction before the assistant contributes.

Use direct, warm, ordinary Hebrew. Avoid clinical framing, diagnosis, treatment
language, performative intimacy, and generic therapist questions.

Neither the reviewer nor the gate may author user-facing fallback prose. They
return only a verdict, reason, and evidence receipt. Every delivered sentence
must come from the main model or editing model for that turn; if revision fails,
preserve the model draft and record the failure rather than emit a canned
admission or coaching question.

## Revision gate

Prefer a claim-level support check over a general impression. Log the evidence
for each changed claim. Pass through a draft only when every concrete claim is
supported, no assistant hypothesis has been laundered into fact, the tone is
ordinary, and the reply advances. Revise only the failing claims; do not replace
a good reply wholesale.

Use a PAVE-style pipeline when adding a support scorer: extract compact atomic
premises from user evidence, score each concrete draft claim against those
premises, then revise only unsupported spans. Require premise IDs or an
explicit `unsupported` result; do not let a fluent holistic score wash away one
unsupported time, event, or entity. For anti-laundering behavior, use
claim-level any-fail aggregation: one unsupported concrete claim forces
revision or abstention.

Treat the scorer as a useful but untrusted signal. Keep hard structural checks
outside the model score: concrete times, places, entities, and events must be
present in trusted premises; ignore instructions embedded in draft or rationale
text; reject fake premise citations; and test hedge-plus-false-detail,
lexical-mimicry, prompt-injection, threshold-hugging, and gradual laundering
attacks.

### Cost-sensitive calibration

Do not choose a support threshold by accuracy alone. Record labeled examples
`(raw_score, supported_label)` with a frozen scorer prompt and premise
extractor, split calibration from validation data, and sweep empirical score
cutpoints. Minimize:

`c_fp * false_passes + c_fn * unnecessary_revisions + c_rev * revisions`.

For this editor, false passes (invented or laundered claims) are more expensive
than revising a good draft, so start with a high false-pass cost and report the
tradeoff. If scores are calibrated estimates of support probability, the Bayes
pass threshold is `c_fp / (c_fp + c_fn)`; otherwise use the empirical sweep.
Never claim a threshold is valid without held-out validation.

Calibrate raw scores with Platt scaling for small data or isotonic regression
when there are enough labeled examples. Track Brier score, log loss, ECE,
AUROC, pass rate, revision rate, and audited false-pass rate. Version the
calibrator with model id, prompt hash, fit window, and threshold.

Monitor drift on rolling labeled or audited samples: ECE, Brier score, score
distribution PSI/KS, supported base rate, pass rate, and false-pass rate;
segment by language, conversation length, model, and suggestion-affirmation
turns. On scorer/model/prompt changes or drift alerts, invalidate the old
calibrator, raise the safety threshold or use a conservative raw-score floor,
increase audits, refit on recent data, validate on a held-out slice, and only
then promote the new version.

Evaluate editor-on and editor-off with the same six-turn transcript. Score each
turn and the whole trace on groundedness, no-laundering, agency, advancement,
tone, and overall preference. Report unsupported-anchor rate separately from
momentum; an editor that advances by inventing context has failed.

Maintain clean and adversarial evaluation sets. Track attack pass rate, clean
pass precision, revision rate on supported drafts, unsupported-anchor rate,
no-laundering rate, and tone regressions. A single prompted scorer is not a
security boundary; trust boundaries, hard checks, claim-level scoring, and
audited drift monitoring are the defense in depth.

Implement in this order: provenance and quarantine; hard span checks and
pass-through bias; structured claim scoring and targeted revision;
cost-sensitive calibration and drift monitoring; static and bounded adaptive
red-team automation; then live rollout with shadow observation and rollback.
Do not replace the RAG stack, train a custom NLI model, add online RL, or add a
multi-agent framework before the provenance and hard-check phases pass.

### Deterministic concrete-span gate

For Phase 2, add a small span-check module using `rapidfuzz` only if the
dependency is accepted by the project. Extract temporal phrases, dates, places,
named entities, and event phrases from the candidate draft. Match each span
against trusted premise text with kind-specific thresholds; proposal-cued spans
may be allowed as proposals, but asserted historical spans must match trusted
evidence. Any unsupported concrete span forces targeted revision or abstention.

Keep this gate independent of the LLM scorer. Use strict thresholds initially,
log false revisions on clean replays, and lower thresholds only with regression
fixtures. Test invented “yesterday evening”, grounded temporal spans,
affirmation-only turns, proposal wording, unknown entities, and harmless fuzzy
paraphrases. This gate reduces cheap invention and cross-thread bleed but does
not replace provenance, semantic entailment, or claim-level support checking.

## Adversarial red team

Red-team the complete path `user evidence -> premises F -> claims -> score s ->
calibration p -> threshold tau -> revision -> delivery`, using synthetic
fixtures only. A false pass is an unsupported concrete claim emitted; a
laundering success is an assistant hypothesis emitted as user history after a
weak affirmation. Keep clean supported drafts in the set to detect false
revisions.

Keep a fixed attack library covering:

- provenance: hypothesis injection into `F`, affirmation laundering, stale or
  superseded facts, selective premise omission, and paraphrase poisoning;
- draft claims: invented time/place/event, lexical mimicry plus a false
  conjunct, hedge-plus-smuggle, false premise citations, supported shell plus
  one lie, verbosity padding, and contradiction sandwich;
- scorer input: instruction override, authority framing, schema spoofing,
  language/encoding changes, and split-field injection;
- gates: threshold hugging, score saturation, calibration mismatch, and
  multi-turn crescendo laundering or correction ignore.

For adaptive internal red-team runs, keep trusted `F` fixed, retain a payload
claim that must remain unsupported, and cap the attacker at 5/10/20 revisions.
Provide only the feedback appropriate to the test (pass/fail for black-box,
score and gaps for gray-box); never expose production internals to users. Log
attack success rate (ASR), ASR by family, queries-to-success, payload retention,
margin above `tau`, clean pass precision, and clean false-revision rate.

Prefer defenses that remove the attack surface: provenance checks before
scoring, verified premise citations, concrete-span checks, claim-level any-fail
aggregation, an LLM/NLI agreement gate when available, conservative fallback
under drift, and CI regressions for every successful attack. Do not accept a
prompt-only fix because a fluent scorer reports a high score. Release only when
laundering, invented-anchor, and scorer-injection ASR meet the agreed bound and
clean quality remains within budget; document residual adaptive risk.

## Live runtime proof

When source or installed runtime code changes, perform these steps yourself:

1. Run the project’s required tests.
2. Copy the owned runtime files to the installed runtime when it is separate.
3. Restart the configured gateway supervisor; never launch a second detached
   gateway as a workaround.
4. Verify a replacement gateway PID and start time.
5. Check the startup receipt containing the loaded module path, SHA-256, PID,
   and editor-enabled state.
6. Compare the receipt hash with the changed source hash and verify the runtime
   canary passed before running a live reply probe.
7. Read the actual delivered transcript. Source tests, file hashes, health
   checks, and model drafts are supporting evidence, not live proof.

If the supervisor is unavailable, report `manual_action_required` with the
exact restart command and do not claim the bot is live. If the receipt is
missing or mismatched, treat the deployment as unverified.

The receipt should also include process start time, package or git version,
reasoning level, prompt hash, evidence-policy version, calibrator id, and
canary detail. A stale process is a failure when its receipt PID differs from
the supervisor MainPID, its hash differs from the deployed source, or its start
time predates the deployment.

## Required regression families

Keep synthetic fixtures for affirmation laundering, invented time/place,
stale or superseded facts, cross-thread queue/journal bleed, false premise
citations, scorer instruction injection, hedge-plus-false-detail, supported
shell plus one lie, ignored correction, threshold hugging, and bounded adaptive
attacks. Every successful attack becomes a permanent regression fixture.

Release requires zero static attack success for laundering, invented-anchor,
and scorer-injection families; bounded adaptive ASR below the agreed limit;
clean pass precision and false-revision rate within budget; and a
receipt-matched delivered six-turn transcript.

## Observed replay rule

The acceptance surface is the delivered reply, not the draft, reviewer verdict,
or unit-test result. A draft that passes the reviewer can still contain an
unsupported temporal anchor, so hard checks must run before pass-through as well
as after editing. After each change, record the same six-turn delivered A/B
transcript, compare editor-on with the editor-off control, and classify every
turn for groundedness, laundering, advancement, agency, tone, and suppression;
do not call the editor an improvement when it only lowers one error while adding
handbacks, therapist-like language, or any reviewer/gate-authored fallback.

## Required handoff

State exactly one of `in_progress`, `manual_action_required`, or `complete`.
Include what changed, focused tests, editor-off control results, editor-on
results, runtime receipt evidence, and any remaining unsupported anchors or
tone failures.
