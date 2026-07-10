import { describe, expect, it } from 'vitest'

import type { SessionInfo } from '@/hermes'

import {
  activeRuntimeSessionStatus,
  continueFromDropoffWithStaleRuntimeRecovery,
  profileRestoreSessionId,
  resolveProfileRestoreSessionId,
  sameCronSignature,
  shouldSettleBusyFromLiveStatus,
  storedSessionIdForCompressionContinuation
} from './desktop-controller-utils'

const session = (id: string, title: string | null): SessionInfo => ({ id, title }) as SessionInfo

describe('sameCronSignature', () => {
  it('is false when the lengths differ', () => {
    expect(sameCronSignature([session('a', 't')], [])).toBe(false)
  })

  it('is true when ids and titles match in order', () => {
    const a = [session('a', 'one'), session('b', 'two')]
    const b = [session('a', 'one'), session('b', 'two')]
    expect(sameCronSignature(a, b)).toBe(true)
  })

  it('is false when a title changed', () => {
    const a = [session('a', 'one')]
    const b = [session('a', 'renamed')]
    expect(sameCronSignature(a, b)).toBe(false)
  })

  it('is false when order differs', () => {
    const a = [session('a', 't'), session('b', 't')]
    const b = [session('b', 't'), session('a', 't')]
    expect(sameCronSignature(a, b)).toBe(false)
  })
})

describe('storedSessionIdForCompressionContinuation', () => {
  it('keeps the visible conversation anchored to the parent stored session', () => {
    expect(storedSessionIdForCompressionContinuation('parent-stored')).toBe('parent-stored')
  })
})

describe('live session status reconciliation', () => {
  it('finds the active runtime session status by live id', () => {
    expect(
      activeRuntimeSessionStatus(
        [
          { id: 'other-runtime', status: 'working' },
          { id: 'active-runtime', session_key: 'stored-session', status: 'idle' }
        ],
        'active-runtime'
      )
    ).toBe('idle')
  })

  it('does not match stored session keys when reconciling runtime state', () => {
    expect(activeRuntimeSessionStatus([{ id: 'runtime-id', session_key: 'stored-id', status: 'idle' }], 'stored-id')).toBe('')
  })

  it('settles only on explicit idle live status', () => {
    expect(shouldSettleBusyFromLiveStatus('idle')).toBe(true)
    expect(shouldSettleBusyFromLiveStatus('working')).toBe(false)
    expect(shouldSettleBusyFromLiveStatus('waiting')).toBe(false)
    expect(shouldSettleBusyFromLiveStatus('')).toBe(false)
  })
})

describe('profileRestoreSessionId', () => {
  it('prefers the remembered session id for the profile', () => {
    const sessions = [
      { ...session('remembered-session', 'Remembered'), last_active: 100, profile: 'film-maker' },
      { ...session('newest-loaded', 'New'), last_active: 200, profile: 'film-maker' }
    ] as SessionInfo[]

    expect(profileRestoreSessionId('film-maker', 'remembered-session', sessions)).toBe('remembered-session')
  })

  it('does not restore a remembered session when the loaded row belongs to another profile', () => {
    const sessions = [
      { ...session('film-last', 'Film'), last_active: 300, profile: 'film-maker' },
      { ...session('office-new', 'Office'), last_active: 200, profile: 'office-work' }
    ] as SessionInfo[]

    expect(profileRestoreSessionId('office-work', 'film-last', sessions)).toBe('office-new')
  })

  it('does not trust an unloaded remembered session id without profile validation', () => {
    const sessions = [{ ...session('film-loaded', 'Film'), last_active: 300, profile: 'film-maker' }] as SessionInfo[]

    expect(profileRestoreSessionId('office-work', 'office-remembered', sessions)).toBeNull()
  })

  it('falls back to the newest loaded session in that profile', () => {
    const sessions = [
      { ...session('default-newest', 'Default'), last_active: 300, profile: 'default' },
      { ...session('film-old', 'Old'), last_active: 100, profile: 'film-maker' },
      { ...session('film-new', 'New'), last_active: 200, profile: 'film-maker' }
    ] as SessionInfo[]

    expect(profileRestoreSessionId('film-maker', null, sessions)).toBe('film-new')
  })

  it('ignores compression ancestors and other profiles', () => {
    const sessions = [
      { ...session('compressed', 'Compressed'), end_reason: 'compression', last_active: 300, profile: 'film-maker' },
      { ...session('other-profile', 'Other'), last_active: 200, profile: 'office-work' }
    ] as SessionInfo[]

    expect(profileRestoreSessionId('film-maker', null, sessions)).toBeNull()
  })
})

describe('resolveProfileRestoreSessionId', () => {
  it('validates an unloaded remembered session id against the target profile before restoring it', async () => {
    const probe = async (sessionId: string, profile: string): Promise<SessionInfo> =>
      ({ ...session(sessionId, 'Office'), profile }) as SessionInfo

    await expect(resolveProfileRestoreSessionId('office-work', 'office-remembered', [], probe)).resolves.toBe(
      'office-remembered'
    )
  })

  it('fails closed to a loaded target-profile session when remembered validation misses', async () => {
    const sessions = [
      { ...session('office-loaded', 'Office'), last_active: 100, profile: 'office-work' }
    ] as SessionInfo[]

    const probe = async (): Promise<SessionInfo> => {
      throw new Error('session not found')
    }

    await expect(resolveProfileRestoreSessionId('office-work', 'bad-remembered', sessions, probe)).resolves.toBe(
      'office-loaded'
    )
  })

  it('does not probe a remembered id that is already known to belong to another profile', async () => {
    const sessions = [
      { ...session('film-last', 'Film'), last_active: 300, profile: 'film-maker' },
      { ...session('office-loaded', 'Office'), last_active: 100, profile: 'office-work' }
    ] as SessionInfo[]

    let probes = 0

    const probe = async (): Promise<SessionInfo> => {
      probes += 1
      throw new Error('should not probe')
    }

    await expect(resolveProfileRestoreSessionId('office-work', 'film-last', sessions, probe)).resolves.toBe(
      'office-loaded'
    )
    expect(probes).toBe(0)
  })
})

describe('continueFromDropoffWithStaleRuntimeRecovery', () => {
  const params = {
    cwd: '/work',
    error: 'Context length exceeded',
    parentSessionId: 'parent-stored',
    pendingPrompt: 'continue',
    profile: 'bina',
    runtimeSessionId: 'stale-runtime'
  }

  it('resumes the stored parent and retries once when the runtime session is gone', async () => {
    const calls: { method: string; params?: Record<string, unknown> }[] = []
    let continueAttempts = 0

    const requestGateway = async <T,>(method: string, requestParams?: Record<string, unknown>): Promise<T> => {
      calls.push({ method, params: requestParams })

      if (method === 'session.continue_from_dropoff') {
        continueAttempts += 1

        if (continueAttempts === 1) {
          throw new Error('session not found')
        }

        return { session_id: 'child-runtime', stored_session_id: 'child-stored' } as T
      }

      if (method === 'session.resume') {
        return { session_id: 'recovered-runtime' } as T
      }

      return {} as T
    }

    const continued = await continueFromDropoffWithStaleRuntimeRecovery(requestGateway, params)

    expect(continued.session_id).toBe('child-runtime')
    expect(calls.map(call => call.method)).toEqual([
      'session.continue_from_dropoff',
      'session.resume',
      'session.continue_from_dropoff'
    ])
    expect(calls[0]?.params).toMatchObject({
      parent_session_id: 'parent-stored',
      pending_prompt: 'continue',
      session_id: 'stale-runtime'
    })
    expect(calls[1]?.params).toEqual({ session_id: 'parent-stored' })
    expect(calls[2]?.params).toMatchObject({
      parent_session_id: 'parent-stored',
      pending_prompt: 'continue',
      session_id: 'recovered-runtime'
    })
  })

  it('does not retry non-stale continuation failures', async () => {
    const requestGateway = async <T,>(method: string): Promise<T> => {
      if (method === 'session.continue_from_dropoff') {
        throw new Error('provider failed')
      }

      return {} as T
    }

    await expect(continueFromDropoffWithStaleRuntimeRecovery(requestGateway, params)).rejects.toThrow('provider failed')
  })
})
