# Life-Boat baseline and release gate

This is a release contract for the Life-Boat instance only. A candidate change is
not an improvement because it passes unit tests; it must beat the preserved
baseline in a replay of the same support turns.

## Baseline

- Preserve the last user-accepted behavior before a candidate change.
- Keep a reversible runtime copy and record its source/config hashes.
- Never store raw vulnerable user text in the baseline artifact.

## Release-blocking regressions

Reject or roll back a candidate if it introduces any of these:

- internal tool, skill, progress, or self-talk messages;
- more messages than the baseline for one user turn;
- a long stitched response, canned question, forced menu, or premature closure;
- repeated interpretation without new user information;
- advice or emotional commands that imply the user controls their feelings;
- unsolicited follow-up contact or memory capture;
- weaker crisis handling, privacy, consent, Hebrew/RTL, or thread routing.

## Required comparison

For every candidate, replay the same anonymized scenarios covering thought loops,
self-criticism, depressive thoughts, corrections, mixed Hebrew/RTL, explicit
safety signals, user-led pauses, and ordinary turns. Record only aggregate
metrics and reviewer decisions. A candidate must show a clear user-facing gain,
with zero severe regressions, before it can reach Life-Boat.

## Rollback

Rollback is the default response to a live regression. Restore the last accepted
runtime snapshot, restart the gateway, verify process identity and delivery
silence, then investigate locally. No GitHub Action is evidence of safety or
production correctness.
