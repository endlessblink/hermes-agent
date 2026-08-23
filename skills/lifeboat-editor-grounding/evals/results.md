# Smoke evaluation — iteration 1

These are workflow checks, not a claim about model quality.

| Eval | Result | Evidence |
|---|---|---|
| Assistant suggestion is not promoted after “yes” | PASS | The skill separates `user_facts` from `assistant_hypotheses` and treats unsupported anchors as failures. |
| Grounded advancing draft is preserved | PASS | The revision gate prefers pass-through and limits edits to unsupported spans. |
| Runtime update is proven before live testing | PASS | The live gateway receipt reported the loaded module, matching SHA-256, PID 571840, editor enabled, and the runtime canary passed. |

Remaining product-level evaluation: repeat the six-turn editor-on/off transcript
and score groundedness, no-laundering, advancement, agency, and tone separately.

# Smoke evaluation — iteration 2

The current live replay is better on grounding but not yet a broad quality win.

| Eval | Result | Evidence |
|---|---|---|
| Installed runtime is the tested runtime | PASS | Source and installed editor hashes matched `e631c47a...`; startup receipt showed editor enabled, PID 906826, and the configured gateway process. |
| Unsupported temporal anchor is blocked before pass-through | PASS | The same six-turn replay no longer delivered the draft's invented “איפה היית אתמול בערב?” anchor; the hard guard forced repair. |
| Overall response quality is improved | INCONCLUSIVE | Editor-on produced grounded, more concrete turns, but also exposed context handback and no-read behavior; human preference and advancement still need review. |

Do not promote this iteration as a finished quality improvement until a fresh
delivered transcript shows no material regression in warmth, agency, or forward
movement against the editor-off control.

# Smoke evaluation — iteration 3

The steering variants were added to the reviewer and the editor-on arm improved
again, but the replay's external-state isolation check failed and remains a
release blocker.

| Eval | Result | Evidence |
|---|---|---|
| Steering variants are caught | PASS | Focused review tests cover “מאיזה רגע אתה רוצה להתחיל?”, “איזו תחושה עולה לך ראשונה?”, and the observed first-day form. |
| Editor-on delivered replies avoid the observed temporal laundering | PASS | The receipt-matched six-turn replay did not deliver the unsupported “אתמול” anchor; current-message anchors and hedged reads appeared instead. |
| Replay isolation remains intact | FAIL | The same run reported that the external Obsidian turn-log folder changed; the cause is not yet established, so the transcript is not clean release evidence. |
| Overall quality is proven better | INCONCLUSIVE | Advancement and tone improved in several turns, but control comparison still includes no-read and fallback-rewrite branches. |

# Smoke evaluation — implementation checkpoint

| Eval | Result | Evidence |
|---|---|---|
| Existing debrief claim/shape checker is on the delivery reviewer path | PASS | The reviewer now evaluates broad debrief replies with the current user text plus optional evidence, and the invented-event regression is covered. |
| Local regression coverage after wiring | PASS | 201 focused tests passed, including debrief shape, reviewer, editor, rewrite, context, runtime, and gate tests. |
| Installed runtime after wiring | PASS | Reviewer and rewrite hashes match source; the restarted gateway receipt reports editor enabled and the runtime check passed. |
| Clean post-wiring delivered replay | PENDING | Requires explicit approval for another cloud-model run after the earlier shared turn-log mutation. |
