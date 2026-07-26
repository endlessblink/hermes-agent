import { useEffect } from 'react'

import { isSessionGoneError } from '@/app/session/hooks/use-session-actions/utils'
import { getSessionMessages, PROMPT_SUBMIT_REQUEST_TIMEOUT_MS } from '@/hermes'
import { type ChatMessage, preserveLocalAssistantErrors, toChatMessages } from '@/lib/chat-messages'
import { gatewayForProfile } from '@/store/gateway'
import { $sessionTiles, publishSessionState, setSessionTileDelegate } from '@/store/session-states'
import type { SessionResumeResponse } from '@/types/hermes'

import type { usePromptActions } from '../../session/hooks/use-prompt-actions'
import type { useSessionStateCache } from '../../session/hooks/use-session-state-cache'
import type { GatewayRequester } from '../types'

type SessionStateCache = ReturnType<typeof useSessionStateCache>

interface SessionTileDelegateParams {
  archiveSession: (storedSessionId: string) => Promise<unknown>
  branchStoredSession: (storedSessionId: string) => Promise<unknown>
  executeSlashCommand: ReturnType<typeof usePromptActions>['executeSlashCommand']
  removeSession: (storedSessionId: string) => Promise<unknown>
  requestGateway: GatewayRequester
  runtimeIdByStoredSessionIdRef: SessionStateCache['runtimeIdByStoredSessionIdRef']
  sessionStateByRuntimeIdRef: SessionStateCache['sessionStateByRuntimeIdRef']
  updateSessionState: SessionStateCache['updateSessionState']
}

const PROFILE_TILE_STAGE_TIMEOUT_MS = 4_500

async function withTileStageTimeout<T>(promise: Promise<T>, stage: string, timeoutMs: number): Promise<T> {
  let timeoutId: ReturnType<typeof setTimeout> | null = null

  try {
    return await Promise.race([
      promise,
      new Promise<never>((_, reject) => {
        timeoutId = setTimeout(() => reject(new Error(`${stage} timed out`)), timeoutMs)
      })
    ])
  } finally {
    if (timeoutId) {
      clearTimeout(timeoutId)
    }
  }
}

export async function preflightProfileOwnedTile(
  storedSessionId: string,
  profile: string,
  readMessages: typeof getSessionMessages = getSessionMessages,
  timeoutMs = PROFILE_TILE_STAGE_TIMEOUT_MS
) {
  try {
    return await withTileStageTimeout(
      readMessages(storedSessionId, profile),
      'Profile session lookup',
      timeoutMs
    )
  } catch (error) {
    if (isSessionGoneError(error)) {
      throw error
    }

    return null
  }
}

export async function resumeProfileOwnedTile(
  request: (method: string, params: Record<string, unknown>) => Promise<SessionResumeResponse>,
  storedSessionId: string,
  timeoutMs = PROFILE_TILE_STAGE_TIMEOUT_MS
) {
  return withTileStageTimeout(
    request('session.resume', { session_id: storedSessionId, cols: 96 }),
    'Profile session resume',
    timeoutMs
  )
}

export function canReuseCachedTileRuntime(
  tiles: Array<{ runtimeId?: string; storedSessionId: string }>,
  storedSessionId: string,
  runtimeId: string | undefined,
  cachedStoredSessionId: string | undefined
): boolean {
  return Boolean(
    runtimeId &&
      cachedStoredSessionId === storedSessionId &&
      tiles.some(tile => tile.storedSessionId === storedSessionId && tile.runtimeId === runtimeId)
  )
}

export function mergeResumedTileMessages(
  currentMessages: ChatMessage[],
  hydratedMessages: ChatMessage[],
  previousRuntimeMessages: ChatMessage[] = []
): ChatMessage[] {
  const base = currentMessages.length > 0 ? currentMessages : hydratedMessages

  return preserveLocalAssistantErrors(base, previousRuntimeMessages)
}

/**
 * Publishes the session-tile delegate: resume / submit / interrupt / slash for
 * tiled sessions WITHOUT touching the primary view ($activeSessionId /
 * $messages stay the main thread's). Resume reuses a live runtime binding when
 * one exists (incl. the main thread's own session); a cold tile binds +
 * hydrates the cache, which publishSessionState mirrors to the tile.
 */
export function useSessionTileDelegate({
  archiveSession,
  branchStoredSession,
  executeSlashCommand,
  removeSession,
  requestGateway,
  runtimeIdByStoredSessionIdRef,
  sessionStateByRuntimeIdRef,
  updateSessionState
}: SessionTileDelegateParams): void {
  useEffect(() => {
    setSessionTileDelegate({
      archiveSession: async storedSessionId => {
        await archiveSession(storedSessionId)
      },
      branchSession: async storedSessionId => {
        await branchStoredSession(storedSessionId)
      },
      deleteSession: async storedSessionId => {
        await removeSession(storedSessionId)
      },
      executeSlash: async (rawCommand, sessionId) => {
        await executeSlashCommand(rawCommand, { sessionId })
      },
      interruptSession: async runtimeId => {
        await requestGateway('session.interrupt', { session_id: runtimeId })
      },
      resumeTile: async (storedSessionId, profile) => {
        const existing = runtimeIdByStoredSessionIdRef.current.get(storedSessionId)
        const cached = existing ? sessionStateByRuntimeIdRef.current.get(existing) : undefined

        if (
          existing &&
          cached &&
          canReuseCachedTileRuntime(
            $sessionTiles.get(),
            storedSessionId,
            existing,
            cached?.storedSessionId ?? undefined
          )
        ) {
          publishSessionState(existing, cached)

          return existing
        }

        // A profile-owned persisted tile has a durable database row. Check it
        // before asking session.resume to wake a worker: a gone id otherwise
        // leaves the tile waiting forever while the authoritative REST lookup
        // has already returned 404.
        const [profilePrefetch, profileGateway] = profile
          ? await Promise.all([
              preflightProfileOwnedTile(storedSessionId, profile),
              withTileStageTimeout(gatewayForProfile(profile), 'Profile gateway startup', PROFILE_TILE_STAGE_TIMEOUT_MS)
            ])
          : [null, null]

        if (profile && !profileGateway) {
          throw new Error(`Hermes gateway is unavailable for ${profile}`)
        }

        const resume = profileGateway
          ? resumeProfileOwnedTile(
              (method, params) => profileGateway.request<SessionResumeResponse>(method, params),
              storedSessionId
            )
          : requestGateway<SessionResumeResponse>('session.resume', { session_id: storedSessionId, cols: 96 })

        const [prefetch, resumed] = profile
          ? [profilePrefetch, await resume]
          : await Promise.all([getSessionMessages(storedSessionId).catch(() => null), resume])

        const runtimeId = resumed?.session_id

        if (!runtimeId) {
          throw new Error('resume returned no session id')
        }

        updateSessionState(
          runtimeId,
          state => ({
            ...state,
            busy: Boolean(resumed?.info?.running),
            messages: mergeResumedTileMessages(
              state.messages,
              toChatMessages(prefetch?.messages ?? resumed?.messages ?? []),
              cached?.messages
            )
          }),
          storedSessionId
        )

        return runtimeId
      },
      submitToSession: async (runtimeId, text) => {
        await requestGateway('prompt.submit', { session_id: runtimeId, text }, PROMPT_SUBMIT_REQUEST_TIMEOUT_MS)
      },
      updateSession: (runtimeId, updater) => updateSessionState(runtimeId, updater)
    })
  }, [
    archiveSession,
    branchStoredSession,
    executeSlashCommand,
    removeSession,
    requestGateway,
    runtimeIdByStoredSessionIdRef,
    sessionStateByRuntimeIdRef,
    updateSessionState
  ])
}
