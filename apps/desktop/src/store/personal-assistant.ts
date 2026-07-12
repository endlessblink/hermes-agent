import { atom } from 'nanostores'

import { $gateway } from '@/store/gateway'

export type PersonalAssistantTrigger = 'manual' | 'scheduled'

interface PersonalAssistantStartResult {
  sessionId: string | null
  status: string
}

export interface AssistantStateItem {
  id: string
  title?: string
  summary?: string
  [key: string]: unknown
}

export interface AssistantPendingItem extends AssistantStateItem {
  status?: string
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
  pendingApprovals: AssistantPendingItem[]
  captureProposals: AssistantPendingItem[]
  sync: {
    status: 'fresh' | 'offline' | 'stale' | 'unknown'
    lastCheckedAt: string | null
    lastVerifiedAt: string | null
  }
  unreadCount: number
  episodes: AssistantStateItem[]
}

export type AssistantStateSection =
  | 'blockers'
  | 'capacity'
  | 'commitments'
  | 'deferred'
  | 'focus'
  | 'outcomes'
  | 'preferences'
  | 'sync'

export interface AssistantStateOperation {
  op: 'archive' | 'forget' | 'set' | 'upsert'
  section: AssistantStateSection
  id?: string
  value?: Record<string, unknown>
}

export const $personalAssistantState = atom<AssistantState | null>(null)
export const $personalAssistantPendingCount = atom<number | null>(null)

function gatewayOrThrow() {
  const gateway = $gateway.get()

  if (!gateway) {
    throw new Error('Hermes gateway is unavailable')
  }

  return gateway
}

export function personalAssistantAvailable(profile: string | null | undefined): boolean {
  return profile?.trim() === 'office-work'
}

export async function startPersonalAssistant(
  trigger: PersonalAssistantTrigger
): Promise<PersonalAssistantStartResult> {
  const gateway = gatewayOrThrow()

  const response = (await gateway.request('personal_assistant.start', { trigger })) as {
    canonical_session_id?: unknown
    session_id?: unknown
    status?: unknown
  }

  const sessionId =
    typeof response.canonical_session_id === 'string' && response.canonical_session_id
      ? response.canonical_session_id
      : typeof response.session_id === 'string' && response.session_id
        ? response.session_id
        : null

  const status = typeof response.status === 'string' ? response.status : 'unknown'

  if (trigger === 'manual' && !sessionId) {
    throw new Error('Personal assistant did not return a session')
  }

  return { sessionId, status }
}

export async function openPersonalAssistantHome(): Promise<string> {
  const response = await gatewayOrThrow().request<{
    canonical_session_id: string
    session_id: string
    state: AssistantState
    status: 'ready'
  }>('personal_assistant.home', { profile: 'office-work' })

  const destinationSessionId = response.canonical_session_id || response.state.sessionId

  if (!destinationSessionId) {
    throw new Error('Personal assistant home did not return a session')
  }

  $personalAssistantState.set(response.state)
  $personalAssistantPendingCount.set(
    (response.state.pendingApprovals?.length ?? 0) + (response.state.captureProposals?.length ?? 0)
  )

  return destinationSessionId
}

export async function refreshPersonalAssistantState(): Promise<AssistantState> {
  const response = await gatewayOrThrow().request<{ state: AssistantState }>('personal_assistant.state.get', {
    profile: 'office-work'
  })

  $personalAssistantState.set(response.state)
  $personalAssistantPendingCount.set(
    (response.state.pendingApprovals?.length ?? 0) + (response.state.captureProposals?.length ?? 0)
  )

  return response.state
}

export async function patchPersonalAssistantState(
  operations: AssistantStateOperation[]
): Promise<AssistantState> {
  const current = $personalAssistantState.get()

  if (!current) {
    throw new Error('Personal assistant state is not loaded')
  }

  const response = await gatewayOrThrow().request<{ state: AssistantState }>('personal_assistant.state.patch', {
    expectedVersion: current.version,
    operations,
    profile: 'office-work'
  })

  $personalAssistantState.set(response.state)
  $personalAssistantPendingCount.set(
    (response.state.pendingApprovals?.length ?? 0) + (response.state.captureProposals?.length ?? 0)
  )

  return response.state
}
