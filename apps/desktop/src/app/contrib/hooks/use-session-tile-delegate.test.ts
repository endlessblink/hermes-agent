import { describe, expect, it, vi } from 'vitest'

import {
  canReuseCachedTileRuntime,
  mergeResumedTileMessages,
  preflightProfileOwnedTile,
  resumeProfileOwnedTile
} from './use-session-tile-delegate'

describe('mergeResumedTileMessages', () => {
  it('keeps the visible backend-restart explanation when a new runtime hydrates the durable transcript', () => {
    const durable = [{ id: 'user-1', role: 'user' as const, parts: [{ type: 'text' as const, text: 'נסה שוב' }] }]
    const recovery = [
      {
        error: 'Hermes restarted before this answer finished.',
        id: 'local-recovery',
        parts: [],
        pending: false,
        role: 'assistant' as const
      }
    ]

    expect(mergeResumedTileMessages([], durable, recovery).map(message => message.id)).toEqual([
      'user-1',
      'local-recovery'
    ])
  })
})

describe('canReuseCachedTileRuntime', () => {
  it('rejects a cached runtime after the owning profile gateway reopened', () => {
    expect(
      canReuseCachedTileRuntime(
        [{ storedSessionId: 'assistant-home' }],
        'assistant-home',
        'stale-runtime',
        'assistant-home'
      )
    ).toBe(false)
  })

  it('reuses a cache only while the tile still owns that exact runtime', () => {
    expect(
      canReuseCachedTileRuntime(
        [{ runtimeId: 'live-runtime', storedSessionId: 'assistant-home' }],
        'assistant-home',
        'live-runtime',
        'assistant-home'
      )
    ).toBe(true)
  })
})

describe('preflightProfileOwnedTile', () => {
  it('rejects a persisted profile-owned tile whose durable session is gone', async () => {
    const readMessages = vi.fn().mockRejectedValue(new Error('404: Session not found'))

    await expect(preflightProfileOwnedTile('dead-session', 'office-work', readMessages)).rejects.toThrow(
      'Session not found'
    )
  })

  it('rejects a persisted profile-owned tile when the gateway returns structured gone-session payload', async () => {
    const readMessages = vi.fn().mockRejectedValue(new Error('Error invoking remote method \'session.resume\': Error: {"error":{"code":4007,"message":"session not found"}}'))

    await expect(preflightProfileOwnedTile('dead-session', 'office-work', readMessages)).rejects.toThrow('session.resume')
  })

  it('allows a temporary transcript read failure to fall through to live resume', async () => {
    const readMessages = vi.fn().mockRejectedValue(new Error('gateway reconnecting'))

    await expect(preflightProfileOwnedTile('assistant-home', 'office-work', readMessages)).resolves.toBeNull()
  })

  it('bounds a cold profile transcript lookup instead of leaving the tab waking forever', async () => {
    const readMessages = vi.fn(() => new Promise<never>(() => undefined))

    await expect(preflightProfileOwnedTile('assistant-home', 'office-work', readMessages, 5)).resolves.toBeNull()
  })
})

describe('resumeProfileOwnedTile', () => {
  it('bounds the final live resume instead of leaving the tab waking forever', async () => {
    const request = vi.fn(() => new Promise<never>(() => undefined))

    await expect(resumeProfileOwnedTile(request, 'assistant-home', 5)).rejects.toThrow(
      'Profile session resume timed out'
    )
  })
})
