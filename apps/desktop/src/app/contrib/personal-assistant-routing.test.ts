import { describe, expect, it, vi } from 'vitest'

import { openPersonalAssistantDestination } from './personal-assistant-routing'

describe('openPersonalAssistantDestination', () => {
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
