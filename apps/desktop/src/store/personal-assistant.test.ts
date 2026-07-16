import { atom } from 'nanostores'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const request = vi.fn()
const gatewayForProfile = vi.fn(async () => ({ request }))
const $gateway = atom<unknown>({})

vi.mock('@/store/gateway', () => ({ $gateway, gatewayForProfile }))

const {
  $personalAssistantState,
  acknowledgePersonalAssistantRead,
  hydratePersonalAssistantStateWhenReady,
  openPersonalAssistantHome
} = await import('./personal-assistant')

const state = (version: number, unreadCount: number) => ({
  blockers: [],
  capacity: { summary: null, updatedAt: null },
  captureProposals: [],
  commitments: [],
  deferred: [],
  episodes: [],
  focus: null,
  outcomes: [],
  pendingApprovals: [],
  schemaVersion: 1 as const,
  sessionId: 'assistant-home',
  sync: { lastCheckedAt: null, lastVerifiedAt: null, status: 'unknown' as const },
  unreadCount,
  version
})

beforeEach(() => {
  request.mockReset()
  gatewayForProfile.mockClear()
  gatewayForProfile.mockResolvedValue({ request })
  $personalAssistantState.set(null)
})

describe('personal assistant home', () => {
  it('hydrates unread state once through the office-work owner gateway', async () => {
    request.mockResolvedValue({ state: state(4, 3) })

    await Promise.all([
      hydratePersonalAssistantStateWhenReady('open'),
      hydratePersonalAssistantStateWhenReady('open')
    ])

    expect(gatewayForProfile).toHaveBeenCalledTimes(1)
    expect(gatewayForProfile).toHaveBeenCalledWith('office-work')
    expect(request).toHaveBeenCalledWith('personal_assistant.state.get', { profile: 'office-work' })
    expect($personalAssistantState.get()?.unreadCount).toBe(3)
  })

  it('retries a failed hydrate and accepts newer unread state', async () => {
    request.mockRejectedValueOnce(new Error('owner gateway starting'))

    await expect(hydratePersonalAssistantStateWhenReady('open')).rejects.toThrow('owner gateway starting')

    request.mockResolvedValueOnce({ state: state(4, 1) })
    await hydratePersonalAssistantStateWhenReady('open')
    expect($personalAssistantState.get()?.unreadCount).toBe(1)

    request.mockResolvedValueOnce({ state: state(5, 3) })
    await hydratePersonalAssistantStateWhenReady('open')
    expect($personalAssistantState.get()?.unreadCount).toBe(3)
  })

  it('opens the canonical home and keeps the returned backend snapshot', async () => {
    request.mockResolvedValue({
      canonical_session_id: 'assistant-home',
      session_id: 'assistant-live',
      state: state(5, 0),
      status: 'ready'
    })

    await expect(openPersonalAssistantHome()).resolves.toBe('assistant-home')

    expect(request).toHaveBeenCalledWith('personal_assistant.home', { profile: 'office-work' })
    expect($personalAssistantState.get()).toEqual(state(5, 0))
  })

  it('re-reads when a read acknowledgement is older than visible attention', async () => {
    $personalAssistantState.set(state(7, 1))
    request.mockResolvedValueOnce({ state: state(6, 0) }).mockResolvedValueOnce({ state: state(8, 0) })

    await expect(acknowledgePersonalAssistantRead()).resolves.toEqual(state(8, 0))

    expect(request).toHaveBeenNthCalledWith(1, 'personal_assistant.read', { profile: 'office-work' })
    expect(request).toHaveBeenNthCalledWith(2, 'personal_assistant.state.get', { profile: 'office-work' })
  })
})
