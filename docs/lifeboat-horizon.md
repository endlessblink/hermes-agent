# Life-Boat horizon

What is finished, what is deliberately not being done, and what comes next.
Written 2026-08-23. The rule this document exists to enforce: nothing stays
loose. Every item below is landed, scheduled, or explicitly discarded — never
merely mentioned.

## Landed and running

Delivery is gated. Engine notices, duplicate sends, canned re-entry sentences
and superseded replies are all stopped before they reach the topic, and the
gates announce themselves at startup and every six hours rather than failing
open in silence.

The bot reads what Noam keeps. Parked subjects, journal lines, weekly rollups.
Monthly, quarterly and yearly rollups now exist and build from the level below.
The pattern note maintains itself from his own dated observations.

The system checks itself. A drift check compares the code tree against the
running install and discovers modules present in only one. A connection audit
reports any artifact nothing writes or nothing reads — currently clean. A
failure matrix generated from the tests holds thirteen real incidents with 109
regressions behind them.

Tests are honest. They no longer read the real session database or write into
the live cron store, they run against the real Telegram library rather than a
stand-in that disagreed with it, and every task claiming coverage must name a
test.

## Next, in order

1. **Adaptive back-off.** A subject not engaged with waits longer each time it
   could resurface; at a month it stops being offered but is never forgotten.
   Explicit dismissal drops it permanently.
2. **A correction affordance.** A lightweight way to mark a reply wrong that
   feeds the back-off and the pattern note, so corrections teach instead of
   evaporating.
3. **Blind A/B against the baseline.** The baseline is frozen and the rollback
   is tested; comparing a candidate against it through an independent path is
   the last piece of the release gate.
4. **Inbound coalescing.** Rapid consecutive messages should form one turn.
   Written on the recovery branch, not salvaged: it touches modules main has
   moved past and needs redoing against current code.

The review-log patterns and the stress matrix came off this list on
2026-08-23. Twelve of the twenty-six patterns are now decided by a check that
runs before delivery and fourteen carry a recorded reason no check can decide
them; every failure-matrix row is driven through the gate that stops it, and
the suite fails if a row loses its gate or a new pattern arrives unowned.

## Waiting on Noam, not on work

- The authenticated conversation gate: a real exchange, reviewed by a person.
  The tooling exists and fails closed; only the review is missing.
- Whether ops alerts belong in the Life-Boat topic at all.
- Whether the check-in cadence and the rollup times suit him.

## Deliberately not doing

- **A pattern matrix of interpretations.** A permanent list of inferences about
  a person is how a feared reading becomes a fact. Patterns stay descriptions of
  what he wrote, anchored to dates, correctable by him.
- **Inferring readiness or mood.** Any rule that depends on a model correctly
  judging someone's inner state will be confidently wrong and cost more trust
  than it earns. Rules run over things he actually said.
- **Merging the recovery branch wholesale.** Its useful content is salvaged;
  the rest would revert work that has moved past it.
- **A second store for memory.** The vault is the graph. A parallel database
  would become a competing source of truth.

## The standing rule

Every user-visible string needs a per-mode policy and a regression in the same
change. Every artifact needs a producer and a consumer. Every task claiming
coverage names its test. When something is found to be disconnected, it is
connected or deleted — not noted.
