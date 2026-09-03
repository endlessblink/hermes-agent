import { act, cleanup } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createClientSessionState } from '@/lib/chat-runtime'
import { $notifications, clearNotifications } from '@/store/notifications'
import { $activeSessionId, $selectedStoredSessionId } from '@/store/session'

import { renderMessageStream } from './test-harness'

const ACTIVE_SID = 'active-runtime'
const BACKGROUND_SID = 'background-runtime'

function complete(sessionId: string, text = 'Background reply') {
  return { payload: { text }, session_id: sessionId, type: 'message.complete' } as const
}

describe('background completion notifications', () => {
  beforeEach(() => {
    clearNotifications()
    $activeSessionId.set(ACTIVE_SID)
    $selectedStoredSessionId.set(null)
  })

  afterEach(() => {
    cleanup()
    clearNotifications()
    $activeSessionId.set(null)
    $selectedStoredSessionId.set(null)
    vi.restoreAllMocks()
  })

  it('restores an in-app bubble that opens the completed background chat', () => {
    const openSession = vi.fn()
    const states = new Map([[BACKGROUND_SID, { ...createClientSessionState(), storedSessionId: 'stored-background-chat' }]])
    const stream = renderMessageStream(ACTIVE_SID, { openSession, states })

    act(() => stream.handleEvent(complete(BACKGROUND_SID)))

    const notification = $notifications.get()[0]
    expect(notification).toMatchObject({ id: `gateway-complete:${BACKGROUND_SID}`, kind: 'success', message: 'Background reply' })

    notification.action?.onClick()
    expect(openSession).toHaveBeenCalledWith('stored-background-chat')
  })

  it('does not duplicate the visible chat when a runtime id is reminted for its stored session', () => {
    const states = new Map([
      [ACTIVE_SID, { ...createClientSessionState(), storedSessionId: 'stored-chat' }],
      [BACKGROUND_SID, { ...createClientSessionState(), storedSessionId: 'stored-chat' }]
    ])

    $selectedStoredSessionId.set('stored-chat')
    const stream = renderMessageStream(ACTIVE_SID, { states })

    act(() => stream.handleEvent(complete(BACKGROUND_SID)))

    expect($notifications.get()).toEqual([])
  })

  it('notifies after leaving an unscoped running chat', () => {
    const stream = renderMessageStream(ACTIVE_SID)

    act(() => stream.handleEvent({ payload: {}, type: 'message.start' }))
    stream.setActiveSessionId(null)
    act(() => stream.handleEvent({ payload: { text: 'Finished after switching chats' }, type: 'message.complete' }))

    expect($notifications.get()).toEqual([
      expect.objectContaining({
        id: `gateway-complete:${ACTIVE_SID}`,
        kind: 'success',
        message: 'Finished after switching chats',
      }),
    ])
  })

  it('notifies when a fresh draft replaces the completed runtime chat', () => {
    const stream = renderMessageStream(ACTIVE_SID)

    $activeSessionId.set('fresh-draft-runtime')

    act(() => stream.handleEvent(complete(ACTIVE_SID, 'Finished after opening a fresh draft')))

    expect($notifications.get()).toEqual([
      expect.objectContaining({
        id: `gateway-complete:${ACTIVE_SID}`,
        kind: 'success',
        message: 'Finished after opening a fresh draft',
      }),
    ])
  })
})
