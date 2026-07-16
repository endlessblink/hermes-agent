import type { SessionInfo } from '@/hermes'
import type { SessionCreateResponse } from '@/types/hermes'

type GatewayRequest = <T>(method: string, params?: Record<string, unknown>, timeoutMs?: number) => Promise<T>
type ProfileSessionProbe = (sessionId: string, profile: string) => Promise<SessionInfo>

const SAVED_TURN_RECOVERY_COOLDOWN_MS = 5 * 60 * 1_000

export type CompressionContinuationResponse = SessionCreateResponse & {
  continued_from_session_id?: string
  dropoff_message_count?: number
  recoverable_turn?: {
    kind: 'continue_interrupted' | 'restart_interrupted'
    recovery_claim_id: string
    text: string
    user_ordinal: number
  }
}

interface ContinueFromDropoffParams {
  cwd?: string
  error: string
  parentSessionId: string
  profile?: string
  runtimeSessionId: string
}

export interface LiveSessionStatusRow {
  id?: string
  pending_prompt?: {
    choices?: string[]
    kind?: string
    question?: string
    request_id?: string
  }
  session_key?: string
  status?: string
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

export function canAutoRecoverSavedTurn(lastAttemptAt: number | undefined, now = Date.now()): boolean {
  return lastAttemptAt === undefined || now - lastAttemptAt > SAVED_TURN_RECOVERY_COOLDOWN_MS
}

export function compressionRecoveryTarget(
  parentStoredSessionId: string,
  sessions: SessionInfo[],
  fallbackProfile: string | undefined
): { profile: string | undefined; sessionId: string } {
  const saved = sessions
    .filter(session => session.id === parentStoredSessionId || session._lineage_root_id === parentStoredSessionId)
    .sort((a, b) => sessionRecency(b) - sessionRecency(a))[0]

  return {
    profile: saved?.profile || fallbackProfile,
    sessionId: saved?.id || parentStoredSessionId
  }
}

export function activeRuntimeSessionRow(
  rows: LiveSessionStatusRow[] | null | undefined,
  runtimeSessionId: string | null | undefined,
  storedSessionId?: string | null
): LiveSessionStatusRow | null {
  const id = runtimeSessionId?.trim()
  const storedId = storedSessionId?.trim()

  if (!id && !storedId) {
    return null
  }

  return rows?.find(item => item?.id === id) ?? rows?.find(item => item?.session_key === storedId) ?? null
}

export function activeRuntimeSessionStatus(
  rows: LiveSessionStatusRow[] | null | undefined,
  runtimeSessionId: string | null | undefined,
  storedSessionId?: string | null
): string {
  const row = activeRuntimeSessionRow(rows, runtimeSessionId, storedSessionId)

  return typeof row?.status === 'string' ? row.status : ''
}

export function shouldSettleBusyFromLiveStatus(status: string | null | undefined): boolean {
  return status === 'idle'
}

export async function recoverSameSessionFromCompression(
  requestGateway: GatewayRequest,
  params: ContinueFromDropoffParams
): Promise<CompressionContinuationResponse> {
  const resumed = await requestGateway<CompressionContinuationResponse>('session.resume', {
    claim_recoverable_turn: true,
    ...(params.profile ? { profile: params.profile } : {}),
    session_id: params.parentSessionId,
    source: 'desktop'
  })

  const recoveredRuntimeId = resumed.session_id?.trim()

  const recovery = resumed.recoverable_turn

  if (!recoveredRuntimeId || !recovery?.text?.trim()) {
    throw new Error('The saved turn could not be claimed for same-conversation recovery')
  }

  await requestGateway('prompt.submit', {
    recovery_claim_id: recovery.recovery_claim_id,
    recovery_kind: recovery.kind,
    session_id: recoveredRuntimeId,
    text: recovery.text,
    ...(recovery.kind === 'restart_interrupted' && {
      truncate_before_user_ordinal: recovery.user_ordinal
    })
  })

  return resumed
}
