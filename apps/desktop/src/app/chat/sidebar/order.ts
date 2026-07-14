type SidebarScopedSession = {
  id: string
  profile?: null | string
  _lineage_root_id?: null | string
}

const profileKey = (value: null | string | undefined): string => value?.trim() || 'default'

/**
 * Apply the sidebar profile scope without ever hiding the selected chat.
 * Route restoration and native profile-scope restoration are asynchronous, so
 * they may briefly disagree after boot; the active row is the continuity
 * anchor and must survive that race (including a continuation selected by its
 * lineage-root id).
 */
export function sessionsForSidebarScope<T extends SidebarScopedSession>(
  sessions: T[],
  scope: string,
  activeSessionId: null | string
): T[] {
  const active = activeSessionId?.trim() || ''
  const normalizedScope = profileKey(scope)

  return sessions.filter(
    session =>
      profileKey(session.profile) === normalizedScope ||
      Boolean(active && (session.id === active || session._lineage_root_id === active))
  )
}

/** New ids first, then ids still present in the persisted order. */
export function reconcileFreshFirst(currentIds: string[], orderIds: string[]): string[] {
  const current = new Set(currentIds)
  const retained = orderIds.filter(id => current.has(id))
  const retainedSet = new Set(retained)

  return [...currentIds.filter(id => !retainedSet.has(id)), ...retained]
}

export function resolveManualSessionOrderIds(currentIds: string[], orderIds: string[], manual: boolean): string[] {
  if (!manual || !currentIds.length || !orderIds.length) {
    return []
  }

  const current = new Set(currentIds)
  const retained = orderIds.filter(id => current.has(id))

  if (!retained.length) {
    return []
  }

  return reconcileFreshFirst(currentIds, orderIds)
}

/** Reorder `items` by `orderIds`; items missing from the order surface first. */
export function orderByIds<T>(items: T[], getId: (item: T) => string, orderIds: string[]): T[] {
  if (!orderIds.length) {
    return items
  }

  const byId = new Map(items.map(item => [getId(item), item]))
  const seen = new Set<string>()
  const ordered: T[] = []

  for (const id of orderIds) {
    const item = byId.get(id)

    if (item) {
      ordered.push(item)
      seen.add(id)
    }
  }

  // Items missing from the persisted order are new since it was last
  // reconciled. Callers pass recency-sorted lists (newest first), so surface
  // these at the TOP instead of burying them beneath the saved order —
  // otherwise a brand-new session sinks to the bottom of the sidebar and reads
  // as "my latest session never showed up".
  const fresh = items.filter(item => !seen.has(getId(item)))

  return fresh.length ? [...fresh, ...ordered] : ordered
}

/** Reconcile a persisted order against the live id set (fresh-first). */
export function reconcileOrderIds(currentIds: string[], orderIds: string[]): string[] {
  if (!currentIds.length) {
    return []
  }

  if (!orderIds.length) {
    return currentIds
  }

  return reconcileFreshFirst(currentIds, orderIds)
}

/** True when two id lists are element-for-element identical. */
export function sameIds(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((item, index) => item === right[index])
}
