import { describe, expect, it } from 'vitest'

import type { SessionInfo } from '@/hermes'

import {
  activeRuntimeSessionRow,
  activeRuntimeSessionStatus,
  canAutoRecoverSavedTurn,
  compressionRecoveryTarget,
  profileRestoreSessionId,
  recoverSameSessionFromCompression,
  resolveProfileRestoreSessionId,
  shouldSettleBusyFromLiveStatus,
  storedSessionIdForCompressionContinuation
} from '../app/desktop-controller-utils'
import { sameCronSignature, sessionMessagesSignature } from './session-signatures'

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

describe('compressionRecoveryTarget', () => {
  it('uses the saved conversation profile instead of a transient post-relaunch profile', () => {
    const sessions = [
      {
        ...session('life-session', 'Life advice'),
        last_active: 200,
        profile: 'life-advisor'
      }
    ] as SessionInfo[]

    expect(compressionRecoveryTarget('life-session', sessions, 'default')).toEqual({
      profile: 'life-advisor',
      sessionId: 'life-session'
    })
  })

  it('follows a visible lineage root to its current saved child', () => {
    const sessions = [
      {
        ...session('current-child', 'Continued chat'),
        _lineage_root_id: 'old-parent',
        last_active: 300,
        profile: 'life-advisor'
      }
    ] as SessionInfo[]

    expect(compressionRecoveryTarget('old-parent', sessions, 'default')).toEqual({
      profile: 'life-advisor',
      sessionId: 'current-child'
    })
  })
})

describe('live session status reconciliation', () => {
  it('preserves a pending clarify payload so reconnect can restore the question', () => {
    expect(
      activeRuntimeSessionRow(
        [
          {
            id: 'active-runtime',
            status: 'waiting',
            pending_prompt: {
              choices: ['A', 'B'],
              kind: 'clarify',
              question: 'Which task first?',
              request_id: 'clarify-1'
            }
          }
        ],
        'active-runtime'
      )
    ).toEqual({
      id: 'active-runtime',
      status: 'waiting',
      pending_prompt: {
        choices: ['A', 'B'],
        kind: 'clarify',
        question: 'Which task first?',
        request_id: 'clarify-1'
      }
    })
  })

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

  it('falls back to the stored session key when the cached runtime id drifted', () => {
    const rows = [{ id: 'runtime-current', session_key: 'stored-id', status: 'idle' }]

    expect(activeRuntimeSessionRow(rows, 'runtime-stale', 'stored-id')).toEqual(rows[0])
    expect(activeRuntimeSessionStatus(rows, 'runtime-stale', 'stored-id')).toBe('idle')
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

describe('recoverSameSessionFromCompression', () => {
  const params = {
    cwd: '/work',
    error: 'Context length exceeded',
    parentSessionId: 'parent-stored',
    profile: 'bina',
    runtimeSessionId: 'stale-runtime'
  }

  it('resumes from durable server state without requiring a volatile desktop prompt', async () => {
    const calls: { method: string; params?: Record<string, unknown> }[] = []

    const requestGateway = async <T,>(method: string, requestParams?: Record<string, unknown>): Promise<T> => {
      calls.push({ method, params: requestParams })

      if (method === 'session.resume') {
        return {
          session_id: 'recovered-runtime',
          recoverable_turn: {
            kind: 'restart_interrupted',
            recovery_claim_id: 'claim-1',
            text: 'continue',
            user_ordinal: 3
          }
        } as T
      }

      if (method === 'prompt.submit') {
        return { accepted: true } as T
      }

      return {} as T
    }

    const continued = await recoverSameSessionFromCompression(requestGateway, params)

    expect(continued.session_id).toBe('recovered-runtime')
    expect(calls.map(call => call.method)).toEqual(['session.resume', 'prompt.submit'])
    expect(calls[0]?.params).toEqual({
      claim_recoverable_turn: true,
      profile: 'bina',
      session_id: 'parent-stored',
      source: 'desktop'
    })
    expect(calls[1]?.params).toMatchObject({
      recovery_claim_id: 'claim-1',
      recovery_kind: 'restart_interrupted',
      session_id: 'recovered-runtime',
      text: 'continue',
      truncate_before_user_ordinal: 3
    })
  })

  it('does not retry non-stale continuation failures', async () => {
    const requestGateway = async <T,>(method: string): Promise<T> => {
      if (method === 'session.resume') {
        throw new Error('provider failed')
      }

      return {} as T
    }

    await expect(recoverSameSessionFromCompression(requestGateway, params)).rejects.toThrow('provider failed')
  })
})

describe('canAutoRecoverSavedTurn', () => {
  it('allows the first recovery attempt', () => {
    expect(canAutoRecoverSavedTurn(undefined, 1_000)).toBe(true)
  })

  it('blocks another automatic attempt during the recovery cooldown', () => {
    expect(canAutoRecoverSavedTurn(1_000, 1_000 + 5 * 60 * 1_000)).toBe(false)
  })

  it('allows another attempt after the recovery cooldown', () => {
    expect(canAutoRecoverSavedTurn(1_000, 1_001 + 5 * 60 * 1_000)).toBe(true)
  })
})

describe('sessionMessagesSignature', () => {
  const msg = (role: string, content: string) =>
    ({ role, content }) as Parameters<typeof sessionMessagesSignature>[0][number]

  it('is stable for identical transcripts', () => {
    expect(sessionMessagesSignature([msg('user', 'hi')])).toBe(sessionMessagesSignature([msg('user', 'hi')]))
  })

  it('changes when content changes', () => {
    expect(sessionMessagesSignature([msg('user', 'hi')])).not.toBe(sessionMessagesSignature([msg('user', 'yo')]))
  })

  it('changes when a message is appended', () => {
    const one = [msg('user', 'hi')]
    expect(sessionMessagesSignature(one)).not.toBe(sessionMessagesSignature([...one, msg('assistant', 'hey')]))
  })
})
