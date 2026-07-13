import { atom } from 'nanostores'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AssistantState } from './personal-assistant'

const request = vi.fn()
const foregroundRequest = vi.fn()
const gatewayForProfile = vi.fn(async () => ({ request }))
const $gateway = atom<unknown>({ request: foregroundRequest })

vi.mock('@/store/gateway', () => ({ $gateway, gatewayForProfile }))

const {
  $personalAssistantState,
  acknowledgePersonalAssistantRead,
  openPersonalAssistantHome,
  patchPersonalAssistantState,
  refreshPersonalAssistantState,
  startPersonalAssistant
} = await import('./personal-assistant')

beforeEach(() => {
  request.mockReset()
  foregroundRequest.mockReset()
  gatewayForProfile.mockClear()
  gatewayForProfile.mockResolvedValue({ request })
  $gateway.set({ request: foregroundRequest })
  $personalAssistantState.set(null)
})

describe('startPersonalAssistant', () => {
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

  it('opens the canonical home and retains its live situation state', async () => {
    const state = {
      schemaVersion: 1 as const,
      version: 4,
      sessionId: 'assistant-home',
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

    await expect(openPersonalAssistantHome()).resolves.toBe('assistant-home')
    expect(gatewayForProfile).toHaveBeenCalledWith('office-work')
    expect(request).toHaveBeenCalledWith('personal_assistant.home', { profile: 'office-work' })
    expect($personalAssistantState.get()).toEqual(state)
  })

  it('routes state reads through the owner profile', async () => {
    const state = { schemaVersion: 1 as const, version: 1 } as unknown as AssistantState
    request.mockResolvedValue({ state })

    await refreshPersonalAssistantState()

    expect(gatewayForProfile).toHaveBeenCalledWith('office-work')
    expect(request).toHaveBeenCalledWith('personal_assistant.state.get', { profile: 'office-work' })
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
