import { atom } from 'nanostores'

import type { HermesUiTaskProfileReviewArtifact } from '@/lib/hermes-ui-artifacts'
import { gatewayForProfile } from '@/store/gateway'
import { $activeGatewayProfile, $activeProfile } from '@/store/profile'
import { $activeSessionId } from '@/store/session'

export const PERSONAL_ASSISTANT_OWNER_PROFILE = 'office-work'
export const PERSONAL_ASSISTANT_ACCEPTANCE_PROFILE = 'personal-assistant-acceptance'

export type PersonalAssistantTrigger = 'manual' | 'scheduled'

interface PersonalAssistantStartResult {
  sessionId: string | null
  status: string
}

export interface PersonalAssistantDestination {
  canonicalSessionId: string
  profile?: string
  runtimeSessionId: string
}

export interface PersonalAssistantDayBlock {
  durationMinutes: number | null
  id: string
  priority?: string | null
  startTime: string
  taskId: string
  title: string
}

export interface PersonalAssistantDayPlan {
  blocks: PersonalAssistantDayBlock[]
  capturedAt: string | null
  complete: true
  date: string
  fresh: true
  source: 'flowstate'
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

export interface AssistantCoverageReceipt {
  id: string
  cadence: 'daily' | 'weekly'
  expectedItemIds: string[]
  reviewedItemIds: string[]
  missingItemIds: string[]
  riskItemIds: string[]
  unresolvedItemIds: string[]
  blockingReasons: string[]
  complete: boolean
  allClear: boolean
  createdAt: string
}

export interface AssistantWatchdogStatus {
  heartbeatAt: string | null
  latestAt: string | null
  latestEvent: string | null
  latestSeverity: string | null
  latestTool: string | null
  repairStatus: 'cancelled' | 'candidate_ready' | 'failed' | 'none' | 'queued' | 'running' | 'timed_out' | 'verifying'
  repairTaskId: string | null
  repairUpdatedAt?: string | null
  repairOutcomeCode?: string | null
  startedAt: string | null
  state: 'active' | 'stale' | 'unavailable'
  watchedSources: number
}

export type PersonalAssistantVisibleOutcome =
  | {
      cardRevision: number
      kind: 'progress-question'
      questionId: 'progressReview'
    }
  | {
      cardRevision: number
      kind: 'plan'
      options: Array<{
        actionId?: string
        reason: string
        taskName: string
      }>
    }
  | {
      cardRevision: number
      kind: 'approval' | 'canceled' | 'recovery'
      [key: string]: unknown
    }

export interface PersonalAssistantActiveTurn {
  cardRevision: number | null
  outcome: PersonalAssistantVisibleOutcome | null
  phase: string | null
  revision: number
  submissionId: string | null
}

export interface AssistantState {
  schemaVersion: 1
  version: number
  sessionId: string | null
  activeTurn?: PersonalAssistantActiveTurn | null
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
  protectedItems?: AssistantStateItem[]
  latestCoverageReceipt?: AssistantCoverageReceipt | null
  watchdog?: AssistantWatchdogStatus
}

export type AssistantStateSection =
  | 'blockers'
  | 'capacity'
  | 'commitments'
  | 'captureProposals'
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

export interface PersonalAssistantInterviewResponse {
  action?: 'answer' | 'back' | 'pause'
  customAnswer?: string
  fieldEdits?: Record<string, string | string[]>
  selectedValues?: string[]
}

export interface PersonalAssistantInterviewRespondParams {
  expectedRevision: number
  interviewId: string
  questionId: string
  requestId: string
  response: PersonalAssistantInterviewResponse
  taskId: string
}

export interface PersonalAssistantInterviewRespondResult {
  duplicate: boolean
  interview: unknown
  nextArtifact?: HermesUiTaskProfileReviewArtifact | null
  receipt: unknown
  stateVersion: number
}

export const $personalAssistantState = atom<AssistantState | null>(null)
export const $personalAssistantPendingCount = atom<number | null>(null)
export const $personalAssistantContextOpen = atom(false)
export const $personalAssistantTodayOpen = atom(false)

let stateHydration: Promise<AssistantState> | null = null
let storedStateProfile: string | null = null

$personalAssistantState.subscribe(state => {
  if (state === null) {
    storedStateProfile = null
  }
})

export function isPersonalAssistantSession(sessionId: string, lineageRootId?: string | null): boolean {
  const canonicalSessionId = $personalAssistantState.get()?.sessionId

  return Boolean(canonicalSessionId && (sessionId === canonicalSessionId || lineageRootId === canonicalSessionId))
}

function storePersonalAssistantState(
  state: AssistantState,
  profile: string = PERSONAL_ASSISTANT_OWNER_PROFILE
): AssistantState {
  const current = $personalAssistantState.get()

  if (current && storedStateProfile === profile && state.version < current.version) {
    return current
  }

  storedStateProfile = profile
  $personalAssistantState.set(state)
  $personalAssistantPendingCount.set(
    (state.pendingApprovals?.filter(item => !item.status || item.status === 'pending').length ?? 0) +
      (state.captureProposals?.filter(item => !item.status || item.status === 'pending').length ?? 0)
  )

  return state
}

async function ownerGateway() {
  const gateway = await gatewayForProfile(PERSONAL_ASSISTANT_OWNER_PROFILE)

  if (!gateway) {
    throw new Error('Hermes gateway is unavailable')
  }

  return gateway
}

function activeAssistantProfile(): string {
  return $activeProfile.get() === PERSONAL_ASSISTANT_ACCEPTANCE_PROFILE ||
    $activeGatewayProfile.get() === PERSONAL_ASSISTANT_ACCEPTANCE_PROFILE
    ? PERSONAL_ASSISTANT_ACCEPTANCE_PROFILE
    : PERSONAL_ASSISTANT_OWNER_PROFILE
}

async function activeAssistantGateway() {
  const profile = activeAssistantProfile()
  const gateway = await gatewayForProfile(profile)

  if (!gateway) {
    throw new Error('Hermes gateway is unavailable')
  }

  return { gateway, profile }
}

export async function startPersonalAssistant(trigger: PersonalAssistantTrigger): Promise<PersonalAssistantStartResult> {
  if (activeAssistantProfile() === PERSONAL_ASSISTANT_ACCEPTANCE_PROFILE) {
    const { canonicalSessionId } = await openPersonalAssistantHome()

    return { sessionId: canonicalSessionId, status: 'ready' }
  }

  const gateway = await ownerGateway()

  const response = (await gateway.request('personal_assistant.start', {
    profile: PERSONAL_ASSISTANT_OWNER_PROFILE,
    trigger
  })) as {
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

export async function openPersonalAssistantHome(): Promise<PersonalAssistantDestination> {
  const { gateway, profile } = await activeAssistantGateway()

  const response = await gateway.request<{
    canonical_session_id: string
    session_id: string
    state: AssistantState
    status: 'ready'
  }>(
    profile === PERSONAL_ASSISTANT_ACCEPTANCE_PROFILE ? 'personal_assistant.shadow.home' : 'personal_assistant.home',
    { profile }
  )

  const destinationSessionId = response.canonical_session_id || response.state.sessionId

  if (!destinationSessionId) {
    throw new Error('Personal assistant home did not return a session')
  }

  storePersonalAssistantState(response.state, profile)

  return {
    canonicalSessionId: destinationSessionId,
    profile,
    runtimeSessionId: response.session_id
  }
}

export async function refreshPersonalAssistantState(): Promise<AssistantState> {
  const { gateway, profile } = await activeAssistantGateway()

  const response = await gateway.request<{ state: AssistantState }>(
    profile === PERSONAL_ASSISTANT_ACCEPTANCE_PROFILE
      ? 'personal_assistant.shadow.state.get'
      : 'personal_assistant.state.get',
    { profile }
  )

  return storePersonalAssistantState(response.state, profile)
}

export async function submitPersonalAssistantShadowAction(
  actionId: string,
  cardRevision: number,
  input: Record<string, unknown> = {}
): Promise<AssistantState> {
  const { gateway, profile } = await activeAssistantGateway()

  if (profile !== PERSONAL_ASSISTANT_ACCEPTANCE_PROFILE) {
    throw new Error('Shadow actions require the generated acceptance profile')
  }

  const response = await gateway.request<{ state: AssistantState }>('personal_assistant.shadow.action', {
    actionId,
    cardRevision,
    eventId: `desktop-action:${crypto.randomUUID()}`,
    input,
    profile
  })

  return storePersonalAssistantState(response.state, profile)
}

export async function fetchPersonalAssistantDayPlan(date: string): Promise<PersonalAssistantDayPlan> {
  return (await ownerGateway()).request<PersonalAssistantDayPlan>('personal_assistant.day_plan', {
    date,
    profile: PERSONAL_ASSISTANT_OWNER_PROFILE
  })
}

export async function hydratePersonalAssistantStateWhenReady(gatewayState: string): Promise<AssistantState | null> {
  const current = $personalAssistantState.get()

  if (gatewayState !== 'open' || current) {
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

export async function patchPersonalAssistantState(operations: AssistantStateOperation[]): Promise<AssistantState> {
  const current = $personalAssistantState.get()

  if (!current) {
    throw new Error('Personal assistant state is not loaded')
  }

  const response = await (
    await ownerGateway()
  ).request<{ state: AssistantState }>('personal_assistant.state.patch', {
    expectedVersion: current.version,
    operations,
    profile: PERSONAL_ASSISTANT_OWNER_PROFILE
  })

  return storePersonalAssistantState(response.state)
}

export async function respondToPersonalAssistantInterview(
  params: PersonalAssistantInterviewRespondParams
): Promise<PersonalAssistantInterviewRespondResult> {
  const result = await (
    await ownerGateway()
  ).request<PersonalAssistantInterviewRespondResult>('personal_assistant.interview.respond', {
    ...params,
    profile: PERSONAL_ASSISTANT_OWNER_PROFILE
  })

  const current = $personalAssistantState.get()

  if (current && Number.isFinite(result.stateVersion) && result.stateVersion > current.version) {
    storePersonalAssistantState({ ...current, version: result.stateVersion })
  }

  return result
}

export async function fetchCurrentPersonalAssistantInterview(): Promise<{
  interview: { interviewId?: unknown; interviewRevision?: unknown } | null
  nextArtifact: unknown
}> {
  return (await ownerGateway()).request('personal_assistant.interview.current', {
    profile: PERSONAL_ASSISTANT_OWNER_PROFILE
  })
}

export async function continuePersonalAssistantInterview(text: string, runtimeSessionId?: string): Promise<void> {
  const { gateway, profile } = await activeAssistantGateway()
  const activeRuntimeSessionId = runtimeSessionId || $activeSessionId.get()
  const destination = activeRuntimeSessionId ? null : await openPersonalAssistantHome()

  if (profile === PERSONAL_ASSISTANT_ACCEPTANCE_PROFILE) {
    const submissionId = `desktop:${crypto.randomUUID()}`

    const response = await gateway.request<{ state: AssistantState }>('personal_assistant.shadow.submit', {
      eventId: submissionId,
      profile,
      session_id: activeRuntimeSessionId || destination?.runtimeSessionId,
      submissionId,
      userIntent: text
    })

    storePersonalAssistantState(response.state, profile)

    return
  }

  await gateway.request('prompt.submit', {
    reject_if_busy: true,
    session_id: activeRuntimeSessionId || destination?.runtimeSessionId,
    text
  })
}
