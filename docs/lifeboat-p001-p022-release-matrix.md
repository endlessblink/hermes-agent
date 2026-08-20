# Life-Boat P-001–P-022 release matrix

This matrix mirrors the canonical Life-Boat review log. An entry is not fixed
until its regression test passes and the applicable production surface has been
exercised successfully. Unit tests, source inspection, and deployment success
alone do not close an entry.

| Entry | Correction surface | Required regression / live verification | Status |
| --- | --- | --- | --- |
| P-001 | Emotional coaching skill and turn-state guidance | Complete concrete Hebrew account without asking for more elaboration or a deeper cause when no decision-changing fact is missing | pending |
| P-002 | Thought-record stage tracking and coaching skill | Multi-turn Hebrew thought record advances event → feeling → verdict/demand → known/inferred → evidence-weighted alternative → chosen response without detour | pending |
| P-003 | Evidence-weighted neutrality guidance | Ambiguous non-response explicitly limits self-blame instead of stopping at an unconstrained “unknown” | pending |
| P-004 | Coaching policy and decision sequencing | Dating decision distinguishes possible opener from genuine interest and completes decomposition before advice | pending |
| P-005 | Vision/evidence discipline | Ambiguous WhatsApp screenshot does not produce an asserted read state or hidden interpersonal fact | pending |
| P-006 | Response continuation and closure policy | User asking to work on a concrete issue receives the next bounded processing step, not a polished conclusion | pending |
| P-007 | Decision-support skill | Ambivalent activity with reluctant friend establishes the user’s own decision before drafting a message | pending |
| P-008 | Mixed practical/emotional turn handling | Practical question is answered briefly and the explicitly named wider burden is substantively addressed in the same response | pending |
| P-009 | Telegram operational guidance | Live Telegram instruction distinguishes menu visibility from manual `/new how` execution and gives an access fallback | pending |
| P-010 | Telegram form adapter/runtime | Four Hebrew energy options and `להמשיך` render, select, and submit; schema failure produces usable plain-text fallback | pending |
| P-011 | Flow State mutation proposal policy | Reality refresh produces an exact rename/reschedule preview and asks approval before any mutation | pending |
| P-012 | Persistence wording and receipts | Duration is read back from visible canonical state, or is explicitly labeled proposal/fallback; never falsely called recorded | pending |
| P-013 | Flow State API and Hermes connector | Estimated duration, due time, and project assignment pass preview → approval → apply → receipt → read-back; replay, conflict, and unsupported-field checks pass | verified for production P-013 lane |
| P-014 | Portfolio presentation and task-analysis UI | 50–100-task review shows at most three concrete decision findings, no generic taxonomy, and separates mutation previews | pending |
| P-015 | Canonical emotional queue and resume gate | Resume after compaction reads the queue, continues its sole active item, and preserves all topics with no dropped item | pending |
| P-016 | Deterministic mixed-turn orchestration | Technical task plus three personal points addresses all points and resumes the exact preserved thread after the technical receipt | pending |
| P-017 | Life-Boat scheduler and consent gate | One optional evening invitation; no write before approval and no retry after decline or silence | pending |
| P-018 | Flow State timer/calendar connector and planning policy | Real timer is read accurately, approved timer transition and calendar session are applied, and both canonical states are read back separately | pending |
| P-019 | Professional coverage ledger and context retrieval | Incomplete professional map surfaces unknowns and asks one bounded question instead of issuing a portfolio conclusion | pending |
| P-020 | Context retrieval and bounded intake | Incomplete multi-domain map leads to one small domain question, never an exhaustive inventory request | pending |
| P-021 | Professional relevance gate | Sufficient portfolio-level lane data stops further operational drilling and moves to the next domain | pending |
| P-022 | Commercial viability evidence gate | Technically mature but unvalidated niche product is labeled commercially unvalidated and is not ranked without audience/distribution/payment evidence | pending |

## Release evidence required for every entry

- source or configuration revision used by the running process;
- focused unit and integration test output;
- Hebrew/RTL or platform-specific evidence where the entry requires it;
- authenticated live runtime evidence on the actual affected surface;
- aggregate-only human review for Life-Boat conversation cases, with no raw
  psychological text in artifacts;
- rollback path and explicit reason if the entry remains pending.

## Evidence snapshot — 2026-08-20

- Main-based release source revision: `4266606d5386588d287f689a5fec206c9094434e`.
- Focused regression suite: `565 passed, 4 skipped`; the skips are opt-in
  external live tests, not failures.
- Privacy-safe adversarial replay: `60` synthetic scenarios, `0` failures,
  with no forced-choice menus, internal status leaks, or consentless summaries.
- Authenticated installed CLI gate: P-001 through P-004 passed with synthetic
  Hebrew scenarios; no raw conversation text was retained.
- Authorized authenticated profile gate: isolated P-005 through P-008
  scenarios passed, including a synthetic screenshot case; no raw conversation
  text was retained.
- Installed runtime overlay: gateway process, deployed Life-Boat modules, and
  Telegram adapter all matched successfully.
- Installed Telegram UI gate: manual `/new` is present; four Hebrew energy
  choices, `להמשיך`, and control creation passed.
- Release-line safety check: the known-broken directive-validation harness is
  not enabled; the recovery checkout and its disabled backup remain untouched.
- Installed Life-Boat profile contracts: P-005 through P-008, P-014 through
  P-017, and P-019 through P-022 passed both file and policy-content checks.
- P-005 through P-008 remain pending only until the actual Telegram surface
  receives the required human/runtime review; P-009 through P-010 have the
  installed renderer evidence but need that same surface review. All other
  pending entries likewise require the authenticated conversation gate before
  release status can change.
