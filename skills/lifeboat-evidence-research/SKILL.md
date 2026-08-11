---
name: lifeboat-evidence-research
description: Use whenever researching, designing, evaluating, or changing the Life-Boat psychological support experience, especially conversational pacing, emotional support, depressive thoughts, rumination, self-criticism, safety, memory, proactive contact, Hebrew/RTL behavior, or claims about making the bot more adaptive. Require high-quality evidence, explicit uncertainty, baseline regression checks, and cost-free verification before implementation.
---

# Life-Boat evidence research

Use this skill to turn research into verified Life-Boat behavior. Do not turn a
paper, framework, or dataset into a hardcoded reply pattern without checking the
actual conversation goal and testing against the accepted baseline.

## Non-negotiable operating rules

- Keep the work scoped to Life-Boat; do not change other assistant instances.
- Run the `$sure` gate before implementation. If confidence is not HIGH, keep
  researching and verifying, then rerun the gate automatically.
- Prefer free local work, cached sources, deterministic tests, and the existing
  authenticated runtime. Do not spend money, consume paid inference/API quota,
  invoke CI/CD, or use GitHub Actions unless a specific proof is impossible
  otherwise; record the reason before doing so.
- Never train on or persist raw vulnerable Life-Boat messages. Use anonymized
  descriptors, aggregate metrics, and approved examples only.
- Preserve a rollback point. Reject a candidate that is worse than baseline or
  merely different without a clear user-facing improvement.

## Research protocol

1. Define the behavior question precisely: for example, “How should the agent
   respond when a user describes a thought loop without asking for advice?”
2. Collect about 100 high-quality sources for the complete review, grouped by
   question. Prefer systematic reviews, meta-analyses, randomized trials,
   official guidance, peer-reviewed datasets, human evaluations, and
   longitudinal usability studies. Mark lower-quality sources as context only.
   Preserve source-count integrity: report directly screened sources separately
   from the larger corpus covered by a systematic or umbrella review. Do not
   present a review's screened corpus as individually read sources.
3. For every source, record: population, intervention or dataset, outcome,
   limitations, evidence tier, and the exact Life-Boat behavior it can or cannot
   justify. One source never justifies a runtime rule by itself.
4. Cross-check findings across independent sources. Record contradictions and
   uncertainty instead of smoothing them into a confident recommendation.
5. Convert only convergent findings into a behavior contract and adversarial
   tests. Separate safety invariants from flexible conversational style.
6. Replay the same anonymized scenarios against the accepted baseline. Require
   no severe regressions and at least one meaningful improvement before release.
7. Inspect the real authenticated Life-Boat runtime after local proof. Treat
   source tests, CI results, or HTTP success as insufficient production proof.

## Quality dimensions

Evaluate candidate turns for:

- relevance to the user’s actual words;
- specificity without invented inner states;
- warmth and naturalness without therapeutic performance;
- openness without forced questions, menus, advice, or premature closure;
- adaptive strategy choice across turns rather than reflection-plus-question
  on every turn;
- recognition of depressive thoughts, loops, self-criticism, and crisis signals;
- user agency, consent, privacy, memory boundaries, and low-pressure contact;
- message count, length, repetition, internal chatter, thread routing, and RTL.

## Required report

Return:

1. Evidence coverage and source tiers.
2. Convergent findings and disagreements.
3. Behaviors justified, behaviors not justified, and open questions.
4. Baseline comparison and regression results.
5. Proposed code/test changes, cost, rollback plan, and remaining proof gaps.

Do not claim the goal is complete while real Life-Boat behavior remains
unverified.
