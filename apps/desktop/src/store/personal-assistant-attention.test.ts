import { atom } from 'nanostores'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const dispatchNativeNotification = vi.fn()
const notify = vi.fn()
const refreshPersonalAssistantState = vi.fn(async () => undefined)
const $personalAssistantState = atom<unknown>(null)
const $personalAssistantPendingCount = atom<number | null>(null)

vi.mock('@/store/native-notifications', () => ({ dispatchNativeNotification }))
vi.mock('@/store/notifications', () => ({ notify }))
vi.mock('@/store/personal-assistant', () => ({
  $personalAssistantPendingCount,
  $personalAssistantState,
  refreshPersonalAssistantState
}))

const { handlePersonalAssistantAttention } = await import('./personal-assistant-attention')

beforeEach(() => {
  dispatchNativeNotification.mockClear()
  notify.mockClear()
  refreshPersonalAssistantState.mockClear()
  $personalAssistantState.set({ unreadCount: 0 })
  $personalAssistantPendingCount.set(null)
})

describe('handlePersonalAssistantAttention', () => {
  it('updates payload counters without owner refresh or foreground profile switching', async () => {
    const openHome = vi.fn()

    await handlePersonalAssistantAttention(
      {
        episode_id: 'episode-attention-1',
        kind: 'approval',
        pending_count: 1,
        session_id: 'assistant-home',
        unread_count: 2
      },
      openHome
    )

    expect(refreshPersonalAssistantState).not.toHaveBeenCalled()
    expect($personalAssistantState.get()).toEqual({ unreadCount: 2 })
    expect($personalAssistantPendingCount.get()).toBe(1)
    expect(openHome).not.toHaveBeenCalled()
    expect(notify).toHaveBeenCalledWith(expect.objectContaining({ action: expect.objectContaining({ label: 'View' }) }))
    expect(dispatchNativeNotification).toHaveBeenCalledWith(
      expect.objectContaining({ actions: [{ id: 'view-personal-assistant', text: 'View' }] })
    )

    const notification = notify.mock.calls.at(-1)?.[0]

    notification.action.onClick()
    expect(openHome).toHaveBeenCalledTimes(1)
  })

  it('deduplicates replayed episode events', async () => {
    const payload = {
      episode_id: 'episode-attention-2',
      kind: 'input',
      pending_count: 0,
      session_id: 'assistant-home',
      unread_count: 1
    }

    await expect(handlePersonalAssistantAttention(payload, vi.fn())).resolves.toBe(true)
    await expect(handlePersonalAssistantAttention(payload, vi.fn())).resolves.toBe(false)
  })
})
