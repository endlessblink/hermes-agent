# Life-Boat stabilisation — plan

Status: proposed
Date: 2026-08-22
Design: `2026-08-22-lifeboat-surface-design.md`

## Where things stand

Shipped and running in production as of 19:45 on 2026-08-22:

- One outbound chokepoint. It suppresses; it never rewrites.
- Engine notices gated per mode. Seven wordings covered.
- Exact-repeat suppression.
- The canned-sentence appender deleted outright.
- A five-state mode machine, persisted per conversation, with sticky
  inference, explicit override, and crisis outranking everything.
- Per-mode reply contracts, with violations logged.
- Crisis classifier reconciled across the two copies and hardened against
  quoted material.
- Regression tests for every observed incident, plus two new release metrics.

Built and tested but **not connected**: the Orchestrator handoff. Work mode
decides correctly that a request should leave; nothing dispatches it yet.

Not started: the re-ask on contract violation. A bad draft is logged and
delivered rather than sent back to the model.

## What this plan covers

Six pieces, ordered by what protects the user soonest. Each ships on its own.

### 1. Close the drift hazard — half a day

The afternoon's worst failure was not a bug in logic; it was two copies of the
same file disagreeing until the live gate could not import. The drift check now
covers the Life-Boat modules, but three problems remain:

- `cron/jobs.py` still differs between tree and runtime by 217 lines.
- Nothing runs the drift check automatically; it has to be remembered.
- Installing is a manual file copy, so a partial install is always possible.

Work: reconcile the remaining divergence, run the drift check on gateway start
and refuse to start Life-Boat delivery if its own modules disagree, and make
installing all-or-nothing.

**Done when** a partial or stale install cannot silently produce a gate that
fails open.

### 2. Wire the Orchestrator handoff — one day

Dispatch a work-mode request into the Orchestrator profile in-process, leave the
pointer line behind, and let Orchestrator answer in its own topic.

Risks: the request must not leak Life-Boat conversational context into the other
profile, and a failed dispatch must tell the user rather than vanish.

**Done when** a bug report in Life-Boat is answered in topic 3, the support
conversation stays clean, and a dispatch failure is visible.

### 3. Re-ask instead of accepting a bad draft — one day

On a contract violation, return the draft to the model naming the specific
problem. One retry. If it fails twice, deliver the model's own words and log it.
No generated replacement prose, ever.

**Done when** a checklist in support mode comes back as prose without any
canned sentence being invented.

### 4. Govern the remaining emissions — one to two days

Four of the eleven emission classes are still ungoverned: background-process
dumps, background-task failures, update output, and slash-command refusals.
Each needs a per-mode policy of deliver, summarise, or suppress. The dumps are
the urgent one — they are how a test diff ended up in a support conversation.

**Done when** every emission class has an explicit policy and a test per mode.

### 5. Collapse the scattered call sites — two days

Move the remaining Life-Boat logic out of the gateway module into the package,
leaving exactly two connection points. This is the structural change the design
describes and the one that stops the pattern recurring.

Sequenced last on purpose: it is the largest diff and the least urgent, and it
is far safer once the pieces above are settled.

**Done when** Life-Boat appears in the gateway module only at the inbound and
outbound hooks.

### 6. Fix auto-resume — half a day

Since 2026-08-20 the Life-Boat session cannot auto-resume: a credential read
happens outside its profile scope while multiplexing is on. A queued turn can
therefore sit indefinitely. Unrelated to this work, but it is the user's bot.

**Done when** a queued Life-Boat turn resumes by itself after a restart.

## Working agreement

- One writer at a time. On 2026-08-22 a second agent rewrote a module mid-run
  and left a broken assertion in a shared test. This is a precondition, not a
  preference.
- Verify in the installed runtime, not only in tests. Every gate passed its
  tests while being dead in production.
- Check the clock before judging behaviour. Several rounds were spent on
  screenshots taken minutes before the relevant build was live.
- Any new user-visible string needs a per-mode policy and a regression test in
  the same change.

## Explicitly out of scope

Rebuilding the general gateway, changing other profiles, altering the Telegram
adapter, and the standing divergence in areas unrelated to Life-Boat delivery.
