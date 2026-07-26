import { describe, expect, it, vi } from 'vitest'

import { attemptContribSameSessionRecovery } from './wiring'

describe('active contribution-shell saved-turn recovery', () => {
  it('replays the saved turn once in the selected conversation and refuses an automatic loop', async () => {
    const calls: Array<{ method: string; params?: Record<string, unknown> }> = []
    const notifications: Array<{ message: string; title: string }> = []

    const requestGateway = vi.fn(async <T,>(method: string, params?: Record<string, unknown>): Promise<T> => {
      calls.push({ method, params })

      if (method === 'session.resume') {
        return {
          recoverable_turn: {
            kind: 'continue_interrupted',
            recovery_claim_id: 'claim-1',
            text: 'Continue using the saved tool results.',
            user_ordinal: 2
          },
          session_id: 'runtime-recovered'
        } as T
      }

      return { status: 'streaming' } as T
    })

    const attempts = new Map<string, number>()

    const common = {
      attempts,
      errorMessage: 'Turn stopped after 30 seconds without progress',
      failedRuntimeSessionId: 'runtime-stuck',
      fallbackProfile: 'office-work',
      notifyError: (title: string, message: string) => {
        notifications.push({ message, title })
      },
      parentStoredSessionId: 'stored-conversation',
      requestGateway: requestGateway as Parameters<typeof attemptContribSameSessionRecovery>[0]['requestGateway'],
      sessions: [{ id: 'stored-conversation', profile: 'office-work' }] as Parameters<
        typeof attemptContribSameSessionRecovery
      >[0]['sessions']
    }

    const recovered = await attemptContribSameSessionRecovery(common)

    expect(recovered?.session_id).toBe('runtime-recovered')
    expect(calls.map(call => call.method)).toEqual(['session.resume', 'prompt.submit'])
    expect(calls[0]?.params).toMatchObject({
      claim_recoverable_turn: true,
      profile: 'office-work',
      session_id: 'stored-conversation',
      source: 'desktop'
    })
    expect(calls[1]?.params).toMatchObject({
      recovery_claim_id: 'claim-1',
      recovery_kind: 'continue_interrupted',
      session_id: 'runtime-recovered',
      text: 'Continue using the saved tool results.'
    })

    const refused = await attemptContribSameSessionRecovery(common)

    expect(refused).toBeNull()
    expect(calls.map(call => call.method)).toEqual(['session.resume', 'prompt.submit'])
    expect(notifications).toEqual([
      {
        message: 'Hermes already retried this saved turn. You can retry it manually without being trapped in a loop.',
        title: 'Automatic recovery stopped'
      }
    ])
  })
})
