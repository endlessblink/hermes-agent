import { describe, expect, it } from 'vitest'

import { JsonRpcGatewayClient } from '@hermes/shared'

class FakeSocket extends EventTarget {
  readyState = WebSocket.OPEN
  sent: string[] = []

  close() {}

  send(value: string) {
    this.sent.push(value)
  }
}

describe('JSON-RPC gateway errors', () => {
  it('preserves structured conflict details needed for durable retries', async () => {
    const socket = new FakeSocket()
    const gateway = new JsonRpcGatewayClient({
      socketFactory: () => socket as unknown as WebSocket
    })
    const connected = gateway.connect('ws://example.test')
    socket.dispatchEvent(new Event('open'))
    await connected

    const request = gateway.request('personal_assistant.interview.respond')
    const frame = JSON.parse(socket.sent[0]) as { id: number | string }
    socket.dispatchEvent(
      new MessageEvent('message', {
        data: JSON.stringify({
          error: {
            code: 4093,
            data: {
              code: 'interview_version_conflict',
              latest: { interviewRevision: 5 }
            },
            message: 'Personal Assistant interview version conflict'
          },
          id: frame.id
        })
      })
    )

    await expect(request).rejects.toEqual(
      expect.objectContaining({
        code: 4093,
        data: {
          code: 'interview_version_conflict',
          latest: { interviewRevision: 5 }
        }
      })
    )
  })
})
