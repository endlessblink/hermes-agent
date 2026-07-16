# Current-main personal assistant foundation recovery

## Why this recovery exists

The office-work assistant lane was built on a product branch that is now far
behind `origin/main`. Merging that branch would remove current-main behavior,
and replaying its original desktop commit would resurrect UI modules that
current main deliberately replaced. The safe recovery is a forward port onto
the current gateway, session, sidebar, and renderer seams.

## Cleanup and forward-port plan

1. Preserve current-main behavior with focused regressions for one persistent
   Personal assistant entry, canonical-home opening, owner-profile routing,
   durable unread hydration, and read acknowledgement.
2. Port the assistant state store and gateway home methods without importing
   the removed legacy artifact renderer or older sidebar architecture.
3. Add one current-sidebar entry that opens the backend-owned canonical
   assistant session. Keep state authority in the backend; the renderer only
   caches the returned snapshot and clears unread after an acknowledged read.
4. Run the focused Python and Desktop suites, then typecheck, lint, build, and
   inspect the complete diff before advancing later FlowState reliability
   commits.

## Explicitly rejected during this slice

- Merging or rebasing the stale product branch onto current main.
- Restoring deleted `hermes-ui` artifact modules from the old desktop tree.
- Porting scheduling, monitor delivery, FlowState writers, Notion, or packaged
  deployment before the persistent assistant home is stable on current main.
- Adding a dependency, environment flag, or second source of assistant truth.

## Completion evidence

- A fresh temporary `HERMES_HOME` proves state durability and unread clearing.
- Desktop tests prove the entry always targets the `office-work` owner and
  navigates to the canonical session identity returned by the gateway.
- Existing current-main session/sidebar tests remain green.
