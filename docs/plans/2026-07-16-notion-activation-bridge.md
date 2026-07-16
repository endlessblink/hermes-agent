# Current-main Notion activation bridge

## Product boundary

Notion remains the project-task source of truth. Hermes may read the configured
data source during planning and may create, update, change status, or archive a
page only through an exact preview approved in Hermes Desktop. A Notion task is
activated in FlowState only when the user starts it or approves an exact
personal work block. Notion changes and FlowState activation remain separate
approvals with separate verified receipts.

## Safe recovery and implementation

1. Replay only the five Notion bridge commits onto fresh current main; never
   merge the stale branch that would delete current-main files.
2. Keep every read and write bound to one configured data source, an exact
   writable-property allowlist, page provenance, schema version, and remote
   read-back.
3. Persist preview and ambiguous-write evidence in owner-only local state, and
   recover response loss without issuing an unproven duplicate mutation.
4. Require an expiry-bound, session-scoped Desktop approval for every fresh
   apply. Carry the exact preview through a rendered `hermes-ui` card; do not
   expose approval tokens in model text or stored conversation content.
5. Preserve recovery after restart only for an exact verified receipt or a
   stale already-dispatched operation. A fresh apply without UI approval fails
   closed.

## Verification and rollout gates

- Focused bridge, gateway, existing FlowState approval, system-prompt, and
  standalone-plugin regressions must pass under a temporary `HERMES_HOME`.
- Desktop artifact, trusted-submit, typecheck, and lint verification must pass.
- The plugin remains disabled in the live profile until exact non-secret schema
  configuration and secret-safe token injection are ready.
- Packaged Desktop proof and disposable Notion-page create/update/status/archive
  verification are required before describing the integration as live.
- Production task mutation is never part of repository-local verification.
