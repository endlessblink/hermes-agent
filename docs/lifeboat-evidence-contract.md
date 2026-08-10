# Life-Boat evidence contract

This document turns external evidence into release requirements. It is not a
prompt and it is not permission to train on private Life-Boat conversations.

## Evidence hierarchy

1. Systematic reviews, meta-analyses, randomized trials, and official safety
   guidance.
2. Peer-reviewed dialogue datasets and human evaluations.
3. Longitudinal usability and therapeutic-alliance studies.
4. Expert frameworks and implementation reports.
5. Individual opinion, product marketing, and unsourced advice are context only.

The full review target is 100 high-quality sources. Each source must record its
population, intervention, outcome, limitations, and the specific Life-Boat
behavior it can justify. A source cannot justify a runtime rule by itself.

## Current anchor evidence

- ESConv defines emotional-support dialogue around helping skills and annotated
  support strategies; it also publishes negative examples for low-empathy or
  low-relevance conversations.
- Multi-turn emotional-support work shows that strategy choice and user-state
  tracking must adapt across turns; a single fixed response pattern is not a
  sufficient dialogue policy.
- Motivational-interviewing evaluations score reflections for appropriateness,
  specificity, naturalness, and engagement, and require manual review even when
  model quality is comparable to human-authored reflections.
- Mental-health conversational-agent reviews repeatedly identify repetition,
  misunderstanding, poor error handling, limited interaction, and weak
  relational skills as user-facing failures.
- Therapeutic-alliance studies connect feeling understood and relationship
  quality with engagement, but do not justify dependency, pressure, or
  pretending to be a clinician.
- WHO and UNICEF guidance require safety, autonomy, privacy, accountability,
  crisis pathways, lived-experience input, and monitoring of long-term harms.

## Required implementation consequences

- Select a turn strategy from the current user signal and conversation state;
  never force reflection-plus-question on every turn.
- Evaluate each reflection for relevance, specificity, naturalness, and whether
  it leaves the user room to correct or deepen it.
- Treat repetition, generic interpretations, internal progress messages,
  message floods, and premature closure as release-blocking failures.
- Keep safety detection and crisis support separate from ordinary exploration;
  do not let safety language turn every emotional message into a crisis script.
- Use opt-in memory and contact, with no vulnerable auto-capture.
- Require aggregate baseline comparison plus human review of representative
  turns; automated scores alone cannot establish that a response feels better.

## Research-to-release loop

For each proposed change: run `$sure`; if confidence is below HIGH, gather and
cross-check the missing evidence, then rerun `$sure`. Replay the same anonymized
scenarios against the accepted baseline, reject any severe regression, and only
ship a candidate that has a measurable and human-judged improvement. CI/CD is
not part of this loop unless a required proof cannot be obtained locally or in
the authenticated Life-Boat runtime. Paid inference, external APIs, and other
billable actions are also off by default; use them only when strictly required
for a missing proof and record the reason before doing so.
