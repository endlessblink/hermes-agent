import { describe, expect, it, vi } from 'vitest'

import {
  createPersonalAssistantEntryActions,
  launchPersonalAssistant,
  openPersonalAssistantDestination,
  openPersonalAssistantTab,
  showPersonalAssistantContext
} from './personal-assistant-routing'

describe('showPersonalAssistantContext', () => {
  it('refreshes every time the context opens so new proposals are reviewable', async () => {
    const refresh = vi.fn(async () => undefined)
    const setOpen = vi.fn()

    await showPersonalAssistantContext({ onError: vi.fn(), refresh, setOpen })
    await showPersonalAssistantContext({ onError: vi.fn(), refresh, setOpen })

    expect(setOpen).toHaveBeenNthCalledWith(1, true)
    expect(setOpen).toHaveBeenNthCalledWith(2, true)
    expect(refresh).toHaveBeenCalledTimes(2)
  })
})

describe('launchPersonalAssistant', () => {
  it('keeps scheduled launches in the background', async () => {
    const navigate = vi.fn()
    const refreshState = vi.fn(async () => undefined)
    const start = vi.fn(async () => ({ sessionId: 'assistant-home' }))

    await launchPersonalAssistant('scheduled', { navigate, refreshState, start })

    expect(start).toHaveBeenCalledWith('scheduled')
    expect(refreshState).toHaveBeenCalledTimes(1)
    expect(navigate).not.toHaveBeenCalled()
  })

  it('routes an explicit manual launch to the canonical assistant', async () => {
    const navigate = vi.fn()

    await launchPersonalAssistant('manual', {
      navigate,
      refreshState: vi.fn(async () => undefined),
      start: vi.fn(async () => ({ sessionId: 'assistant-home' }))
    })

    expect(navigate).toHaveBeenCalledWith('/assistant-home')
  })
})

describe('openPersonalAssistantDestination', () => {
  it('keeps the main and notification entries on chat while context stays separate', () => {
    const openChat = vi.fn()
    const openContext = vi.fn()
    const actions = createPersonalAssistantEntryActions({ openChat, openContext })

    actions.primary()
    actions.notification()

    expect(openChat).toHaveBeenCalledTimes(2)
    expect(openContext).not.toHaveBeenCalled()

    actions.context()

    expect(openContext).toHaveBeenCalledTimes(1)
  })

  it('finishes the office-work session rebind before exposing the route', async () => {
    const calls: string[] = []

    const openHome = vi.fn(async () => {
      calls.push('home')

      return {
        canonicalSessionId: 'assistant-home',
        runtimeSessionId: 'assistant-live'
      }
    })

    const resumeSession = vi.fn(async (sessionId: string) => {
      calls.push(`resume:${sessionId}`)
    })

    const navigate = vi.fn((route: string) => {
      calls.push(`navigate:${route}`)
    })

    await openPersonalAssistantDestination({ navigate, openHome, resumeSession })

    expect(calls).toEqual(['home', 'resume:assistant-home', 'navigate:/assistant-home'])
  })

  it('leaves a dead restored route immediately when the durable assistant home is already known', async () => {
    let releaseHome!: (destination: { canonicalSessionId: string; runtimeSessionId: string }) => void
    const openHome = vi.fn(
      () => new Promise<{ canonicalSessionId: string; runtimeSessionId: string }>(resolve => (releaseHome = resolve))
    )
    const navigate = vi.fn()
    const opening = openPersonalAssistantDestination({
      knownSessionId: 'assistant-home',
      navigate,
      openHome,
      resumeSession: vi.fn(async () => undefined)
    })

    expect(navigate).toHaveBeenCalledWith('/assistant-home')
    releaseHome({ canonicalSessionId: 'assistant-home', runtimeSessionId: 'assistant-live' })
    await opening
  })

  it('reads the durable home before waking it when restart hydration has not finished', async () => {
    let releaseHome!: (destination: { canonicalSessionId: string; runtimeSessionId: string }) => void
    const openHome = vi.fn(
      () => new Promise<{ canonicalSessionId: string; runtimeSessionId: string }>(resolve => (releaseHome = resolve))
    )
    const navigate = vi.fn()
    const opening = openPersonalAssistantDestination({
      loadKnownSessionId: vi.fn(async () => 'assistant-home'),
      navigate,
      openHome,
      resumeSession: vi.fn(async () => undefined)
    })

    await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith('/assistant-home'))
    expect(openHome).toHaveBeenCalledTimes(1)
    releaseHome({ canonicalSessionId: 'assistant-home', runtimeSessionId: 'assistant-live' })
    await opening
  })

  it('does not expose a route when the session rebind fails', async () => {
    const navigate = vi.fn()

    await expect(
      openPersonalAssistantDestination({
        navigate,
        openHome: async () => ({
          canonicalSessionId: 'assistant-home',
          runtimeSessionId: 'assistant-live'
        }),
        resumeSession: async () => {
          throw new Error('profile switch failed')
        }
      })
    ).rejects.toThrow('profile switch failed')

    expect(navigate).not.toHaveBeenCalled()
  })
})

describe('openPersonalAssistantTab', () => {
  it('opens the assistant home as a top tab without replacing the current chat', async () => {
    const bindSessionRuntime = vi.fn()
    const focusOpenSession = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true)
    const openSessionTile = vi.fn()

    await openPersonalAssistantTab({
      bindSessionRuntime,
      focusOpenSession,
      openHome: async () => ({ canonicalSessionId: 'assistant-home', runtimeSessionId: 'assistant-live' }),
      openSessionTile
    })

    expect(focusOpenSession).toHaveBeenCalledWith('assistant-home')
    expect(focusOpenSession).toHaveBeenCalledTimes(2)
    expect(openSessionTile).toHaveBeenCalledWith(
      'assistant-home',
      'center',
      undefined,
      undefined,
      'office-work',
      'assistant-live'
    )
    expect(openSessionTile).toHaveBeenCalledTimes(2)
    expect(bindSessionRuntime).toHaveBeenCalledWith('assistant-home', 'assistant-live')
    expect(bindSessionRuntime).toHaveBeenCalledTimes(2)
  })

  it('focuses the existing assistant tab instead of opening a duplicate', async () => {
    const bindSessionRuntime = vi.fn()
    const openSessionTile = vi.fn()

    await openPersonalAssistantTab({
      bindSessionRuntime,
      focusOpenSession: () => true,
      openHome: async () => ({ canonicalSessionId: 'assistant-home', runtimeSessionId: 'assistant-live' }),
      openSessionTile
    })

    expect(openSessionTile).toHaveBeenCalledWith(
      'assistant-home',
      'center',
      undefined,
      undefined,
      'office-work',
      'assistant-live'
    )
    expect(bindSessionRuntime).toHaveBeenCalledWith('assistant-home', 'assistant-live')
  })

  it('waits for a newly opened assistant pane to exist before focusing it', async () => {
    const focusOpenSession = vi
      .fn()
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true)

    await openPersonalAssistantTab({
      bindSessionRuntime: vi.fn(),
      focusOpenSession,
      openHome: async () => ({ canonicalSessionId: 'assistant-home', runtimeSessionId: 'assistant-live' }),
      openSessionTile: vi.fn()
    })

    expect(focusOpenSession).toHaveBeenCalledTimes(3)
    expect(focusOpenSession).toHaveBeenLastCalledWith('assistant-home')
  })
})
