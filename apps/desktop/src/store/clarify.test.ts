import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  $clarifyRequest,
  $clarifyRequests,
  type ClarifyRequest,
  clearClarifyRequest,
  clearClarifyRequestAliasesForSession,
  setClarifyRequest,
  setClarifyRequestAliases
} from './clarify'
import { $activeSessionId } from './session'

function clarify(sessionId: string | null, requestId: string): ClarifyRequest {
  return {
    requestId,
    question: `question-${requestId}`,
    choices: null,
    sessionId
  }
}

describe('clarify store', () => {
  beforeEach(() => {
    $clarifyRequests.set({})
    $activeSessionId.set(null)
  })

  afterEach(() => {
    $clarifyRequests.set({})
    $activeSessionId.set(null)
  })

  it('keeps clarify requests from concurrent sessions independent', () => {
    setClarifyRequest(clarify('session-a', 'req-a'))
    setClarifyRequest(clarify('session-b', 'req-b'))

    expect($clarifyRequests.get()['session-a']?.requestId).toBe('req-a')
    expect($clarifyRequests.get()['session-b']?.requestId).toBe('req-b')
  })

  it('exposes only the active session via the focus-scoped view', () => {
    setClarifyRequest(clarify('session-a', 'req-a'))
    setClarifyRequest(clarify('session-b', 'req-b'))

    $activeSessionId.set('session-a')
    expect($clarifyRequest.get()?.requestId).toBe('req-a')

    $activeSessionId.set('session-b')
    expect($clarifyRequest.get()?.requestId).toBe('req-b')

    $activeSessionId.set('session-c')
    expect($clarifyRequest.get()).toBeNull()
  })

  it('clears only the targeted session, leaving the other pending', () => {
    setClarifyRequest(clarify('session-a', 'req-a'))
    setClarifyRequest(clarify('session-b', 'req-b'))

    clearClarifyRequest('req-a', 'session-a')

    expect($clarifyRequests.get()['session-a']).toBeUndefined()
    expect($clarifyRequests.get()['session-b']?.requestId).toBe('req-b')
  })

  it('ignores a stale clear whose request id no longer matches', () => {
    setClarifyRequest(clarify('session-a', 'req-a2'))

    clearClarifyRequest('req-a1', 'session-a')

    expect($clarifyRequests.get()['session-a']?.requestId).toBe('req-a2')
  })

  it('clears by request id across sessions when no session hint is given', () => {
    setClarifyRequest(clarify('session-a', 'shared'))
    setClarifyRequest(clarify('session-b', 'other'))

    clearClarifyRequest('shared')

    expect($clarifyRequests.get()['session-a']).toBeUndefined()
    expect($clarifyRequests.get()['session-b']?.requestId).toBe('other')
  })

  it('restores one pending question across equivalent assistant session identities', () => {
    setClarifyRequestAliases(clarify(null, 'shared'), [null, 'canonical', 'runtime'])

    for (const activeId of [null, 'canonical', 'runtime']) {
      $activeSessionId.set(activeId)
      expect($clarifyRequest.get()?.requestId).toBe('shared')
    }

    $activeSessionId.set('transient-desktop-id')
    expect($clarifyRequest.get()?.requestId).toBe('shared')

    clearClarifyRequest('shared')
    expect($clarifyRequests.get()).toEqual({})
  })

  it('clears every alias of the request completed by one session', () => {
    setClarifyRequestAliases(clarify(null, 'shared'), ['canonical', 'runtime'])
    setClarifyRequest(clarify('other', 'other-request'))

    clearClarifyRequestAliasesForSession('runtime')

    expect($clarifyRequests.get()['canonical']).toBeUndefined()
    expect($clarifyRequests.get()['runtime']).toBeUndefined()
    expect($clarifyRequests.get()['other']?.requestId).toBe('other-request')
  })
})
