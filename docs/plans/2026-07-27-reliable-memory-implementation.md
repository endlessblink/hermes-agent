# Reliable memory implementation

## Invariants

- SQLite records the append-only event history.
- An event is not active until its managed Obsidian note and manifest are
  atomically written and read back.
- Every retrieval reconciles the managed notes before returning memory.
- A safe manual note edit becomes a new user-authored revision.
- Missing, malformed, conflicting, or unsafe notes fail closed and are excluded
  from retrieval until repaired.
- Corrections are reversible; permanent purge removes both event history and the
  managed note.
- The subsystem is profile-local and disabled by default during shadow rollout.

## Delivery order

1. Lock the repository, mirror, correction, conflict, and concurrency behavior
   with focused tests.
2. Add the ledger and managed-note mirror as a standalone subsystem.
3. Add feature-flagged compatibility routing from the existing memory tool.
4. Add dry-run-first migration from flat, scoped, and Personal Assistant memory.
5. Prove focused tests, full memory/Personal Assistant regressions, backup and
   restore, and packaged restart behavior before enabling the feature.

## Cleanup constraints

- Do not rewrite the existing flat-memory implementation during shadow rollout.
- Reuse the existing threat scanner, profile resolution, atomic replacement,
  vault configuration, and approval gate.
- Do not add dependencies.
- Keep legacy files as rollback inputs until ledger and mirror parity is proven.
