import { atom } from 'nanostores'

import { gatewayForProfile } from '@/store/gateway'

export const PERSONAL_ASSISTANT_OWNER_PROFILE = 'office-work'
export const PERSONAL_ASSISTANT_REFRESH_MS = 30_000

export interface AssistantStateItem {
  id?: string
  title?: string
  summary?: string
  [key: string]: unknown
}

export interface AssistantState {
  schemaVersion: 1
  version: number
  sessionId: string | null
  outcomes: AssistantStateItem[]
  commitments: AssistantStateItem[]
  capacity: { summary: string | null; updatedAt: string | null }
  focus: AssistantStateItem | null
  blockers: AssistantStateItem[]
  deferred: AssistantStateItem[]
  preferences?: AssistantStateItem[]
  pendingApprovals: AssistantStateItem[]
  captureProposals: AssistantStateItem[]
  sync: {
    status: 'fresh' | 'offline' | 'stale' | 'unknown'
    lastCheckedAt: string | null
    lastVerifiedAt: string | null
  }
  unreadCount: number
  episodes: AssistantStateItem[]
  source?: { kind: string; version: number; hash: string | null }
}

export const $personalAssistantState = atom<AssistantState | null>(null)

let stateHydration: Promise<AssistantState> | null = null

function storePersonalAssistantState(state: AssistantState): AssistantState {
  const current = $personalAssistantState.get()

  if (current && state.version < current.version) {
    return current
  }

  $personalAssistantState.set(state)

  return state
}

async function ownerGateway() {
  const gateway = await gatewayForProfile(PERSONAL_ASSISTANT_OWNER_PROFILE)

  if (!gateway) {
    throw new Error('Hermes gateway is unavailable')
  }

  return gateway
}

export async function openPersonalAssistantHome(): Promise<string> {
  const response = await (
    await ownerGateway()
  ).request<{
    canonical_session_id: string
    session_id: string
    state: AssistantState
    status: 'ready'
  }>('personal_assistant.home', { profile: PERSONAL_ASSISTANT_OWNER_PROFILE })

  const destinationSessionId = response.canonical_session_id || response.state.sessionId

  if (!destinationSessionId) {
    throw new Error('Personal assistant home did not return a session')
  }

  storePersonalAssistantState(response.state)

  return destinationSessionId
}

export async function refreshPersonalAssistantState(): Promise<AssistantState> {
  const response = await (
    await ownerGateway()
  ).request<{ state: AssistantState }>('personal_assistant.state.get', {
    profile: PERSONAL_ASSISTANT_OWNER_PROFILE
  })

  return storePersonalAssistantState(response.state)
}

export async function hydratePersonalAssistantStateWhenReady(gatewayState: string): Promise<AssistantState | null> {
  const current = $personalAssistantState.get()

  if (gatewayState !== 'open') {
    return current
  }

  if (!stateHydration) {
    stateHydration = refreshPersonalAssistantState().finally(() => {
      stateHydration = null
    })
  }

  return stateHydration
}

export async function acknowledgePersonalAssistantRead(): Promise<AssistantState> {
  const response = await (
    await ownerGateway()
  ).request<{ state: AssistantState }>('personal_assistant.read', {
    profile: PERSONAL_ASSISTANT_OWNER_PROFILE
  })

  const current = $personalAssistantState.get()

  if (current && response.state.version < current.version) {
    return refreshPersonalAssistantState()
  }

  return storePersonalAssistantState(response.state)
}
