import { afterEach, describe, expect, it, vi } from 'vitest'

import { $clarifyRequests, clearClarifyRequest, setClarifyRequest } from '@/store/clarify'
import type * as SessionStore from '@/store/session'

import { respondToClarifyRequest } from './clarify-response'

vi.mock('@/store/session', async importOriginal => ({
  ...(await importOriginal<typeof SessionStore>()),
  noteSessionActivity: vi.fn()
}))

afterEach(() => clearClarifyRequest())

describe('respondToClarifyRequest', () => {
  it('keeps an accepted request until the turn-complete event clears it', async () => {
    const request = {
      choices: ['Yes', 'No'],
      question: 'Continue?',
      requestId: 'req-1',
      sessionId: 'session-1'
    }

    setClarifyRequest(request)

    await respondToClarifyRequest({
      answer: 'Yes',
      copy: { gatewayDisconnected: '', notReady: '', sendFailed: '' } as never,
      gateway: { request: vi.fn().mockResolvedValue({ ok: true }) } as never,
      request
    })

    expect($clarifyRequests.get()['session-1']).toEqual(request)
  })

  it('uses the chat surface gateway instead of the globally active gateway', async () => {
    const request = {
      choices: ['Yes', 'No'],
      question: 'Continue?',
      requestId: 'req-tile',
      sessionId: 'tile-session'
    }

    const globalRequest = vi.fn()
    const surfaceRequest = vi.fn().mockResolvedValue({ ok: true })

    await respondToClarifyRequest({
      answer: 'Yes',
      copy: { gatewayDisconnected: '', notReady: '', sendFailed: '' } as never,
      gateway: { request: globalRequest } as never,
      request,
      requestGateway: surfaceRequest
    })

    expect(surfaceRequest).toHaveBeenCalledWith('clarify.respond', {
      request_id: 'req-tile',
      answer: 'Yes'
    })
    expect(globalRequest).not.toHaveBeenCalled()
  })

  it('refreshes the session watchdog before resuming the model turn', async () => {
    const { noteSessionActivity } = await import('@/store/session')
    const requestGateway = vi.fn().mockResolvedValue({ ok: true })

    await respondToClarifyRequest({
      answer: 'אז צבע צימות מלא',
      copy: { gatewayDisconnected: '', notReady: '', sendFailed: '' } as never,
      gateway: null,
      request: {
        choices: null,
        question: 'What should I enter?',
        requestId: 'req-watchdog',
        sessionId: 'runtime-session'
      },
      requestGateway,
      watchdogSessionId: 'stored-session'
    })

    expect(noteSessionActivity).toHaveBeenCalledWith('stored-session')
    expect(vi.mocked(noteSessionActivity).mock.invocationCallOrder[0]).toBeLessThan(
      requestGateway.mock.invocationCallOrder[0]
    )
  })
})
