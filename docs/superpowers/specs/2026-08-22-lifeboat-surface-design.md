# Life-Boat surface — design

Status: draft for review
Date: 2026-08-22

## Problem

Life-Boat is a psychological-support conversation that also carries time
management and hands-on work. It has no surface of its own. Its behaviour is
spread across 51 call sites inside a 22,656-line gateway module, and its
"personality" is a post-hoc rewrite applied to the last line of a generic agent
turn. Ten consecutive `fix:` commits each patched one symptom without moving the
boundary that produced it.

## Evidence

Observed in the live Telegram topic on 2026-08-22:

- Engine plumbing delivered mid-conversation: `Context compression finished`
  (16:13), `⚡ Interrupting current task` (15:25, 17:01), `⏳ Queued for the next
  turn` (13:47), `✅ מעבד את ההודעות שקיבלתי ביחד` (13:46).
- One hardcoded Hebrew sentence repeated verbatim at 15:25, 15:26, 15:29, 15:30,
  16:14 and twice inside 17:02. Produced by a fallback that discarded the
  model's ending and appended a fixed string whenever a draft failed the gate.
- A byte-identical multi-line answer delivered twice, at 13:46 and 13:47.
- A raw background-process dump — an entire test diff — pasted into the topic at
  17:08, followed by the same canned sentence.
- Coaching shape imposed on technical answers: a Telegram-delivery explanation
  closed with "רוצה שנחשוב על צעד אחד קטן…".

The last item is the key one. Life-Boat is used for three different things, and
a single coaching contract was applied to all of them.

## Decisions taken

1. Life-Boat stays a full agent. One layer owns everything the topic displays.
2. A draft that violates its contract is re-asked of the model. No templates.
3. Modes are auto-detected, sticky, and overridable by the user.
4. Work mode hands off to Orchestrator (topic 3) rather than answering in place.
5. Handoff is internal. Telegram cannot deliver bot-to-bot messages, and this
   install shares one bot across every topic, so a message-based handoff would
   be silently dropped.

## Architecture

A `gateway/lifeboat/` package with exactly two connections to the gateway: one
inbound hook when a message arrives, one outbound hook before anything is sent.
Every other Life-Boat call site in `gateway/run.py` is deleted.

- `surface.py` — the only code that decides what the topic shows.
- `modes.py` — the state machine: states, transitions, per-mode contract.
- `contracts.py` — per-mode validation (length, shape, question, structure).
- `handoff.py` — dispatch to Orchestrator and the pointer line left behind.
- `state.py` — one versioned, atomically written state file per session,
  replacing the two files that can currently disagree.

`lifeboat_psychology`'s signal classification is kept, demoted from policy
authority to one input into mode selection.

## State machine

States: `SUPPORT` (default), `TIME`, `WORK`, `CRISIS`, `PAUSED`.

- `SUPPORT ↔ TIME ↔ WORK` by sticky auto-detect with hysteresis: leaving a mode
  requires two consecutive user messages classified into the same new mode, or
  an explicit instruction, so the mode cannot flip on one ambiguous sentence.
- `CRISIS` is entered from safety signals, overrides every other contract, and
  is left only on an explicit all-clear.
- `PAUSED` suspends replies and all proactive contact until the user returns.
- Explicit override always wins, by command or plain language.

Transitions live in one pure function with an explicit table, so the whole
machine is exhaustively testable. Every transition is logged with its reason.

## Emission inventory

Eleven classes can currently write into the topic. Only the first two are
governed today; the rest are ungoverned and are the substance of this work.

| # | Emission | Today |
|---|----------|-------|
| 1 | Busy acks: steer, queue, interrupt, subagent, compressing | gated |
| 2 | Status callback: compression, working-N-min, status phrases | gated |
| 3 | Agent reply (contract, duplicate, banned generic) | gated |
| 4 | Background-process completion dumps | ungoverned |
| 5 | Background-task failure notices | ungoverned |
| 6 | Hermes update success/failure output | ungoverned |
| 7 | Slash-command refusals and force-stop notices | ungoverned |
| 8 | Runtime footer line appended to replies | ungoverned |
| 9 | Approval buttons and model-picker cards | ungoverned |
| 10 | Voice replies | ungoverned |
| 11 | Proactive follow-ups and the morning flow | partly |

Each class gets one policy per mode: deliver, summarise, or suppress. Support
and Crisis suppress plumbing; Work delivers it, because there it is the point.

## Reply contract

Per-mode validation of the draft. On violation, the draft returns to the model
with the specific problem named. On a second violation the model's own draft is
delivered and the failure is logged. No canned prose is generated anywhere —
the current fallbacks are deleted, not made smarter.

## Handoff

Work mode passes the request to the Orchestrator profile in-process.
Orchestrator answers in topic 3 with its own tools and voice. Life-Boat leaves a
single pointer line. Handoff is automatic; the user can keep a request local by
saying so.

## Testing

- Exhaustive transition-table tests over the state machine.
- One golden transcript per mode, including Hebrew and RTL.
- One suppression test per emission class per mode.
- Duplicate-delivery and repeated-sentence regression tests.
- Crisis handling tested separately from ordinary coaching.
- Handoff tested for dispatch, pointer line, and no cross-topic leakage.

## Sequencing

Three plans, in order, each shippable on its own:

1. Surface and emission governance — the package skeleton, the two hooks, and a
   policy for all eleven emission classes.
2. The mode state machine and per-mode reply contracts, including the re-ask.
3. The Orchestrator handoff.

Reconciling the installed/source divergence is a precondition for plan 1.

## Migration

Strangler. Build the package with tests, wire the two hooks, then delete the 51
scattered call sites in one commit. Old state files stay readable for one
version. Current behaviour runs untouched until the switch.

## Risks

- This tree has concurrent writers. On 2026-08-22 a second agent rewrote a new
  module mid-run and left a broken assertion in a shared test. Single-writer
  discipline is a precondition for this work.
- The installed runtime has diverged from source in both directions; the
  installed crisis classifier carries negation and provenance handling that the
  source tree lacks. Reconciling that divergence must precede the migration, or
  deploying will silently delete live safety logic.

## Out of scope

Rebuilding the general gateway, changing other profiles, and altering the
Telegram adapter.
