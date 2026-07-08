import type { SessionInfo } from '@/hermes'
import type { SessionCreateResponse } from '@/types/hermes'

type GatewayRequest = <T>(method: string, params?: Record<string, unknown>, timeoutMs?: number) => Promise<T>
type ProfileSessionProbe = (sessionId: string, profile: string) => Promise<SessionInfo>

export type CompressionContinuationResponse = SessionCreateResponse & {
  continued_from_session_id?: string
  dropoff_message_count?: number
}

interface ContinueFromDropoffParams {
  cwd?: string
  error: string
  parentSessionId: string
  pendingPrompt: string
  profile?: string
  runtimeSessionId: string
}

function normalizeSessionProfileKey(name: string | null | undefined): string {
  const value = (name ?? '').trim()

  return value || 'default'
}

function sessionRecency(session: SessionInfo): number {
  return session.last_active || session.started_at || 0
}

export function profileRestoreSessionId(
  profile: string | null | undefined,
  rememberedSessionId: null | string,
  sessions: SessionInfo[]
): null | string {
  const remembered = rememberedSessionId?.trim()
  const profileKey = normalizeSessionProfileKey(profile)

  const rememberedSession = remembered
    ? sessions.find(session => session.id === remembered || session._lineage_root_id === remembered)
    : undefined

  if (remembered && rememberedSession) {
    if (!rememberedSession || normalizeSessionProfileKey(rememberedSession.profile) === profileKey) {
      return remembered
    }
  }

  const newest = sessions
    .filter(
      session => normalizeSessionProfileKey(session.profile) === profileKey && session.end_reason !== 'compression'
    )
    .sort((a, b) => sessionRecency(b) - sessionRecency(a))[0]

  return newest?.id ?? null
}

export async function resolveProfileRestoreSessionId(
  profile: string | null | undefined,
  rememberedSessionId: null | string,
  sessions: SessionInfo[],
  probeSession: ProfileSessionProbe
): Promise<null | string> {
  const remembered = rememberedSessionId?.trim()
  const loadedTarget = profileRestoreSessionId(profile, remembered ?? null, sessions)

  if (!remembered || loadedTarget === remembered) {
    return loadedTarget
  }

  const profileKey = normalizeSessionProfileKey(profile)
  const knownRememberedSession = sessions.find(
    session => session.id === remembered || session._lineage_root_id === remembered
  )

  if (!knownRememberedSession) {
    try {
      const resolved = await probeSession(remembered, profileKey)

      if (normalizeSessionProfileKey(resolved.profile) === profileKey) {
        return remembered
      }
    } catch {
      // Local remembered state can be stale or contaminated; fail closed to
      // the newest loaded row in the selected profile instead of cross-opening.
    }
  }

  return loadedTarget
}

// Cheap signature compare so a poll only swaps the atom (and re-renders the
// sidebar) when the visible rows actually changed.
export function sameCronSignature(a: SessionInfo[], b: SessionInfo[]): boolean {
  if (a.length !== b.length) {
    return false
  }

  return a.every((session, i) => {
    const other = b[i]

    return (
      other != null &&
      session.id === other.id &&
      session._lineage_root_id === other._lineage_root_id &&
      session.title === other.title &&
      session.source === other.source &&
      session.profile === other.profile &&
      session.preview === other.preview &&
      session.message_count === other.message_count &&
      session.last_active === other.last_active &&
      session.ended_at === other.ended_at
    )
  })
}

export function storedSessionIdForCompressionContinuation(parentStoredSessionId: string): string {
  return parentStoredSessionId
}

function isSessionNotFoundError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error)

  return /session not found/i.test(message)
}

function dropoffRequestParams(params: ContinueFromDropoffParams, runtimeSessionId: string): Record<string, unknown> {
  return {
    cwd: params.cwd,
    error: params.error,
    parent_session_id: params.parentSessionId,
    pending_prompt: params.pendingPrompt,
    profile: params.profile,
    session_id: runtimeSessionId
  }
}

export async function continueFromDropoffWithStaleRuntimeRecovery(
  requestGateway: GatewayRequest,
  params: ContinueFromDropoffParams
): Promise<CompressionContinuationResponse> {
  try {
    return await requestGateway<CompressionContinuationResponse>(
      'session.continue_from_dropoff',
      dropoffRequestParams(params, params.runtimeSessionId)
    )
  } catch (err) {
    if (!isSessionNotFoundError(err)) {
      throw err
    }

    const resumed = await requestGateway<{ session_id?: string }>('session.resume', {
      session_id: params.parentSessionId
    })

    const recoveredRuntimeId = resumed.session_id?.trim()

    if (!recoveredRuntimeId) {
      throw err
    }

    return await requestGateway<CompressionContinuationResponse>(
      'session.continue_from_dropoff',
      dropoffRequestParams(params, recoveredRuntimeId)
    )
  }
}
