import { atom, computed } from 'nanostores'

import { $activeSessionId } from './session'

export interface ClarifyRequest {
  requestId: string
  question: string
  choices: string[] | null
  sessionId: string | null
  profile?: string
  allowFocusFallback?: boolean
}

// Pending clarify requests keyed by the runtime session id that raised them.
// Storing per-session (instead of one shared slot) lets a *background* session
// park its clarify request while the user is looking at a different chat, then
// resolve it once they switch over — without a second concurrent clarify
// clobbering the first. A request with no session id lands under the empty key.
const keyFor = (sessionId: string | null | undefined): string => sessionId ?? ''

export const $clarifyRequests = atom<Record<string, ClarifyRequest>>({})

// The clarify request for the currently-viewed session. The inline ClarifyTool
// only ever mounts inside the active session's transcript, so it reads this
// focus-scoped view rather than reaching into the whole map.
export const $clarifyRequest = computed(
  [$clarifyRequests, $activeSessionId],
  (requests, activeId) => {
    const exact = requests[keyFor(activeId)]

    if (exact) {
      return exact
    }

    const pending = Object.values(requests)
    const requestIds = new Set(pending.map(request => request.requestId))

    return requestIds.size === 1 && pending.some(request => request.allowFocusFallback)
      ? (pending[0] ?? null)
      : null
  }
)

/** The clarify request for one specific session — the tile counterpart of the
 *  active-session `$clarifyRequest` view (same map, fixed key). */
export const sessionClarifyRequest = (sessionId: string | null) =>
  computed($clarifyRequests, requests => requests[keyFor(sessionId)] ?? null)

export function setClarifyRequest(request: ClarifyRequest): void {
  $clarifyRequests.set({ ...$clarifyRequests.get(), [keyFor(request.sessionId)]: request })
}

export function setClarifyRequestAliases(
  request: ClarifyRequest,
  sessionIds: Array<string | null | undefined>
): void {
  const next = { ...$clarifyRequests.get() }

  for (const sessionId of sessionIds) {
    const normalizedSessionId = sessionId?.trim() || null

    next[keyFor(normalizedSessionId)] = {
      ...request,
      allowFocusFallback: true,
      sessionId: normalizedSessionId
    }
  }

  $clarifyRequests.set(next)
}

export function clearClarifyRequest(requestId?: string, sessionId?: string | null): void {
  const requests = $clarifyRequests.get()

  // Targeted clear when the caller knows the session (the common path from the
  // inline ClarifyTool answering its own request).
  if (sessionId !== undefined) {
    const key = keyFor(sessionId)
    const current = requests[key]

    if (!current || (requestId && current.requestId !== requestId)) {
      return
    }

    const next = { ...requests }
    delete next[key]
    $clarifyRequests.set(next)

    return
  }

  // Fallback with no session hint: drop every entry matching the request id
  // (or clear all when none is given).
  const next: Record<string, ClarifyRequest> = {}
  let changed = false

  for (const [key, value] of Object.entries(requests)) {
    if (requestId && value.requestId !== requestId) {
      next[key] = value
    } else {
      changed = true
    }
  }

  if (changed) {
    $clarifyRequests.set(next)
  }
}

export function clearClarifyRequestAliasesForSession(sessionId: string | null): void {
  const request = $clarifyRequests.get()[keyFor(sessionId)]

  if (request) {
    clearClarifyRequest(request.requestId)
  }
}
