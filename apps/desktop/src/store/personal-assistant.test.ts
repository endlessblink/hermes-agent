import { atom } from 'nanostores'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AssistantState } from './personal-assistant'
import { $activeSessionId } from './session'

const { $activeGatewayProfile, $activeProfile } = vi.hoisted(() => {
  let gatewayValue = 'default'
  let profileValue = 'default'

  return {
    $activeGatewayProfile: {
      get: () => gatewayValue,
      set: (next: string) => {
        gatewayValue = next
      }
    },
    $activeProfile: {
      get: () => profileValue,
      set: (next: string) => {
        profileValue = next
      }
    }
  }
})

const request = vi.fn()
const foregroundRequest = vi.fn()
const gatewayForProfile = vi.fn(async () => ({ request }))
const $gateway = atom<unknown>({ request: foregroundRequest })

vi.mock('@/store/gateway', () => ({ $gateway, gatewayForProfile }))
vi.mock('@/store/profile', () => ({ $activeGatewayProfile, $activeProfile }))

const {
  $personalAssistantState,
  acknowledgePersonalAssistantRead,
  continuePersonalAssistantInterview,
  fetchPersonalAssistantDayPlan,
  hydratePersonalAssistantStateWhenReady,
  openPersonalAssistantHome,
  patchPersonalAssistantState,
  respondToPersonalAssistantInterview,
  refreshPersonalAssistantState,
  submitPersonalAssistantShadowAction,
  startPersonalAssistant
} = await import('./personal-assistant')

beforeEach(() => {
  request.mockReset()
  foregroundRequest.mockReset()
  gatewayForProfile.mockClear()
  gatewayForProfile.mockResolvedValue({ request })
  $gateway.set({ request: foregroundRequest })
  $activeSessionId.set(null)
  $activeGatewayProfile.set('default')
  $activeProfile.set('default')
  $personalAssistantState.set(null)
})

describe('startPersonalAssistant', () => {
  it('hydrates persisted unread state once when the desktop gateway becomes ready', async () => {
    const state = {
      schemaVersion: 1 as const,
      version: 7,
      unreadCount: 4
    } as unknown as AssistantState

    request.mockResolvedValue({ state })

    await Promise.all([hydratePersonalAssistantStateWhenReady('open'), hydratePersonalAssistantStateWhenReady('open')])

    expect(request).toHaveBeenCalledTimes(1)
    expect($personalAssistantState.get()?.unreadCount).toBe(4)
  })

  it('does not open the owner gateway before the desktop gateway is ready', async () => {
    await hydratePersonalAssistantStateWhenReady('connecting')

    expect(gatewayForProfile).not.toHaveBeenCalled()
  })

  it('routes every profile through the single office-work assistant owner', async () => {
    request.mockResolvedValue({
      session_id: 'assistant-live-1',
      canonical_session_id: 'assistant-home',
      status: 'launched'
    })

    await expect(startPersonalAssistant('manual')).resolves.toEqual({
      sessionId: 'assistant-home',
      status: 'launched'
    })
    expect(gatewayForProfile).toHaveBeenCalledWith('office-work')
    expect(gatewayForProfile.mock.invocationCallOrder[0]).toBeLessThan(request.mock.invocationCallOrder[0])
    expect(foregroundRequest).not.toHaveBeenCalled()
    expect(request).toHaveBeenCalledWith('personal_assistant.start', {
      profile: 'office-work',
      trigger: 'manual'
    })
  })

  it('never starts the production assistant from the generated acceptance profile', async () => {
    $activeGatewayProfile.set('personal-assistant-acceptance')
    request.mockResolvedValue({
      canonical_session_id: 'acceptance-home',
      session_id: 'acceptance-live',
      state: { schemaVersion: 1, version: 1, sessionId: 'acceptance-home' },
      status: 'ready'
    })

    await expect(startPersonalAssistant('manual')).resolves.toEqual({
      sessionId: 'acceptance-home',
      status: 'ready'
    })
    expect(gatewayForProfile).toHaveBeenCalledWith('personal-assistant-acceptance')
    expect(request).toHaveBeenCalledWith('personal_assistant.shadow.home', {
      profile: 'personal-assistant-acceptance'
    })
    expect(request).not.toHaveBeenCalledWith('personal_assistant.start', expect.anything())
  })

  it('uses the generated shadow route while its primary gateway is still activating', async () => {
    $activeProfile.set('personal-assistant-acceptance')
    request.mockResolvedValue({
      canonical_session_id: 'acceptance-home',
      session_id: 'acceptance-live',
      state: { schemaVersion: 1, version: 1, sessionId: 'acceptance-home' },
      status: 'ready'
    })

    await openPersonalAssistantHome()

    expect(gatewayForProfile).toHaveBeenCalledWith('personal-assistant-acceptance')
    expect(request).toHaveBeenCalledWith('personal_assistant.shadow.home', {
      profile: 'personal-assistant-acceptance'
    })
  })

  it('opens the canonical home and retains its live situation state', async () => {
    const state = {
      schemaVersion: 1 as const,
      version: 4,
      sessionId: 'assistant-home',
      activeTurn: {
        submissionId: 'plan-today',
        phase: 'awaiting-context',
        revision: 2,
        cardRevision: 1,
        outcome: {
          kind: 'progress-question' as const,
          cardRevision: 1,
          questionId: 'progressReview' as const
        }
      },
      outcomes: [],
      commitments: [],
      capacity: { summary: 'Three focused hours', updatedAt: '2026-07-12T09:00:00Z' },
      focus: null,
      blockers: [],
      deferred: [],
      pendingApprovals: [],
      captureProposals: [],
      sync: { status: 'fresh' as const, lastCheckedAt: null, lastVerifiedAt: null },
      unreadCount: 2,
      episodes: []
    }

    request.mockResolvedValue({ session_id: 'assistant-live', state, status: 'ready' })

    await expect(openPersonalAssistantHome()).resolves.toEqual({
      canonicalSessionId: 'assistant-home',
      profile: 'office-work',
      runtimeSessionId: 'assistant-live'
    })
    expect(gatewayForProfile).toHaveBeenCalledWith('office-work')
    expect(request).toHaveBeenCalledWith('personal_assistant.home', { profile: 'office-work' })
    expect($personalAssistantState.get()).toEqual(state)
  })

  it('opens the acceptance-only shadow home when that generated profile is active', async () => {
    const state = {
      schemaVersion: 1 as const,
      version: 1,
      sessionId: 'acceptance-home',
      unreadCount: 0
    } as unknown as AssistantState

    $personalAssistantState.set({ ...state, sessionId: 'office-home', version: 999 })
    $activeGatewayProfile.set('personal-assistant-acceptance')
    request.mockResolvedValue({
      canonical_session_id: 'acceptance-home',
      session_id: 'acceptance-live',
      state,
      status: 'ready'
    })

    await expect(openPersonalAssistantHome()).resolves.toEqual({
      canonicalSessionId: 'acceptance-home',
      profile: 'personal-assistant-acceptance',
      runtimeSessionId: 'acceptance-live'
    })
    expect(gatewayForProfile).toHaveBeenCalledWith('personal-assistant-acceptance')
    expect(request).toHaveBeenCalledWith('personal_assistant.shadow.home', {
      profile: 'personal-assistant-acceptance'
    })
    expect($personalAssistantState.get()).toEqual(state)
  })

  it('submits acceptance-profile text through the shadow runtime', async () => {
    const state = {
      schemaVersion: 1 as const,
      version: 2,
      sessionId: 'acceptance-home',
      unreadCount: 0
    } as unknown as AssistantState

    $activeGatewayProfile.set('personal-assistant-acceptance')
    $activeSessionId.set('acceptance-live')
    request.mockResolvedValue({ state })

    await continuePersonalAssistantInterview('Plan the rest of today')

    expect(request).toHaveBeenCalledWith('personal_assistant.shadow.submit', {
      eventId: expect.stringMatching(/^desktop:/),
      profile: 'personal-assistant-acceptance',
      session_id: 'acceptance-live',
      submissionId: expect.stringMatching(/^desktop:/),
      userIntent: 'Plan the rest of today'
    })
    expect($personalAssistantState.get()).toEqual(state)
  })

  it('applies a visible acceptance-profile card action and stores the result', async () => {
    const state = {
      schemaVersion: 1 as const,
      version: 3,
      sessionId: 'acceptance-home',
      unreadCount: 0
    } as unknown as AssistantState

    $activeGatewayProfile.set('personal-assistant-acceptance')
    request.mockResolvedValue({ state })

    await submitPersonalAssistantShadowAction('include:task-one', 2)

    expect(request).toHaveBeenCalledWith('personal_assistant.shadow.action', {
      actionId: 'include:task-one',
      cardRevision: 2,
      eventId: expect.stringMatching(/^desktop-action:/),
      input: {},
      profile: 'personal-assistant-acceptance'
    })
    expect($personalAssistantState.get()).toEqual(state)
  })

  it('routes state reads through the owner profile', async () => {
    const state = { schemaVersion: 1 as const, version: 1 } as unknown as AssistantState
    request.mockResolvedValue({ state })

    await refreshPersonalAssistantState()

    expect(gatewayForProfile).toHaveBeenCalledWith('office-work')
    expect(request).toHaveBeenCalledWith('personal_assistant.state.get', { profile: 'office-work' })
  })

  it('reads the FlowState day plan through the owner profile', async () => {
    const plan = {
      blocks: [{ id: 'block-1', taskId: 'task-1', title: 'Draft plan', startTime: '09:00', durationMinutes: 45 }],
      capturedAt: '2026-07-20T08:00:00Z',
      complete: true,
      date: '2026-07-20',
      fresh: true,
      source: 'flowstate'
    }

    request.mockResolvedValue(plan)

    await expect(fetchPersonalAssistantDayPlan('2026-07-20')).resolves.toEqual(plan)

    expect(gatewayForProfile).toHaveBeenCalledWith('office-work')
    expect(request).toHaveBeenCalledWith('personal_assistant.day_plan', {
      date: '2026-07-20',
      profile: 'office-work'
    })
  })

  it('acknowledges read state through the owner and stores the returned snapshot', async () => {
    const state = {
      schemaVersion: 1 as const,
      version: 2,
      unreadCount: 0
    } as unknown as AssistantState

    request.mockResolvedValue({ state })

    await acknowledgePersonalAssistantRead()

    expect(gatewayForProfile).toHaveBeenCalledWith('office-work')
    expect(request).toHaveBeenCalledWith('personal_assistant.read', { profile: 'office-work' })
    expect($personalAssistantState.get()).toEqual(state)
  })

  it('re-reads instead of overwriting newer attention with a stale read acknowledgement', async () => {
    const current = {
      schemaVersion: 1 as const,
      version: 4,
      unreadCount: 1
    } as unknown as AssistantState

    const staleAcknowledgement = { ...current, version: 3, unreadCount: 0 }
    const refreshed = { ...current, version: 5, unreadCount: 0 }

    $personalAssistantState.set(current)
    request.mockResolvedValueOnce({ state: staleAcknowledgement }).mockResolvedValueOnce({ state: refreshed })

    await expect(acknowledgePersonalAssistantRead()).resolves.toEqual(refreshed)

    expect(request).toHaveBeenNthCalledWith(1, 'personal_assistant.read', { profile: 'office-work' })
    expect(request).toHaveBeenNthCalledWith(2, 'personal_assistant.state.get', { profile: 'office-work' })
    expect($personalAssistantState.get()).toEqual(refreshed)
  })

  it('patches state with optimistic concurrency and stores the returned snapshot', async () => {
    const current = { schemaVersion: 1 as const, version: 4 } as unknown as AssistantState
    $personalAssistantState.set(current)
    request.mockResolvedValue({ state: { ...current, version: 5 } })
    const operations = [{ op: 'archive' as const, section: 'blockers' as const, id: 'blocked-1' }]

    await patchPersonalAssistantState(operations)

    expect(gatewayForProfile).toHaveBeenCalledWith('office-work')
    expect(request).toHaveBeenCalledWith('personal_assistant.state.patch', {
      expectedVersion: 4,
      operations,
      profile: 'office-work'
    })
    expect($personalAssistantState.get()?.version).toBe(5)
  })

  it('commits an interview answer through the dedicated response endpoint', async () => {
    const current = { schemaVersion: 1 as const, version: 4 } as unknown as AssistantState
    $personalAssistantState.set(current)
    request.mockResolvedValue({ duplicate: false, interview: { revision: 9 }, receipt: {}, stateVersion: 5 })

    await respondToPersonalAssistantInterview({
      expectedRevision: 8,
      interviewId: 'weekly-1',
      questionId: 'urgency',
      requestId: 'request-1',
      response: { selectedValues: ['high'] },
      taskId: 'pet-results'
    })

    expect(request).toHaveBeenCalledWith('personal_assistant.interview.respond', {
      expectedRevision: 8,
      interviewId: 'weekly-1',
      profile: 'office-work',
      questionId: 'urgency',
      requestId: 'request-1',
      response: { selectedValues: ['high'] },
      taskId: 'pet-results'
    })
    expect($personalAssistantState.get()?.version).toBe(5)
  })

  it('continues an interview through the canonical owner runtime instead of the active composer', async () => {
    const state = {
      schemaVersion: 1 as const,
      version: 5,
      sessionId: 'assistant-home'
    } as unknown as AssistantState

    request
      .mockResolvedValueOnce({
        canonical_session_id: 'assistant-home',
        session_id: 'assistant-runtime',
        state,
        status: 'ready'
      })
      .mockResolvedValueOnce({ status: 'streaming' })

    await continuePersonalAssistantInterview('Continue committed interview receipt.')

    expect(request).toHaveBeenNthCalledWith(1, 'personal_assistant.home', { profile: 'office-work' })
    expect(request).toHaveBeenNthCalledWith(2, 'prompt.submit', {
      reject_if_busy: true,
      session_id: 'assistant-runtime',
      text: 'Continue committed interview receipt.'
    })
    expect(foregroundRequest).not.toHaveBeenCalled()
  })

  it('continues a pinned interview in the exact runtime that owns the visible card', async () => {
    request.mockResolvedValueOnce({ status: 'streaming' })

    await continuePersonalAssistantInterview('Continue committed interview receipt.', 'visible-assistant-runtime')

    expect(request).toHaveBeenCalledTimes(1)
    expect(request).toHaveBeenCalledWith('prompt.submit', {
      reject_if_busy: true,
      session_id: 'visible-assistant-runtime',
      text: 'Continue committed interview receipt.'
    })
    expect(foregroundRequest).not.toHaveBeenCalled()
  })

  it('continues a main-chat interview in the active visible runtime', async () => {
    $activeSessionId.set('active-assistant-runtime')
    request.mockResolvedValueOnce({ status: 'streaming' })

    await continuePersonalAssistantInterview('Continue committed interview receipt.')

    expect(request).toHaveBeenCalledTimes(1)
    expect(request).toHaveBeenCalledWith('prompt.submit', {
      reject_if_busy: true,
      session_id: 'active-assistant-runtime',
      text: 'Continue committed interview receipt.'
    })
    expect(foregroundRequest).not.toHaveBeenCalled()
  })

  it('uses the same assistant entry point for scheduled starts', async () => {
    request.mockResolvedValue({ session_id: 'assistant-2', status: 'launched' })

    await startPersonalAssistant('scheduled')

    expect(request).toHaveBeenCalledWith('personal_assistant.start', {
      profile: 'office-work',
      trigger: 'scheduled'
    })
  })

  it('fails clearly when a manual start does not produce a session', async () => {
    request.mockResolvedValue({ status: 'already_completed' })

    await expect(startPersonalAssistant('manual')).rejects.toThrow('did not return a session')
  })
})
