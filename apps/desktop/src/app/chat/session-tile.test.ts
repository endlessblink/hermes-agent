import { describe, expect, it } from 'vitest'

import { latestTranscriptClarifyIsPending } from './session-tile'

describe('latestTranscriptClarifyIsPending', () => {
  it('rejects a stale gateway prompt after the transcript completed it', () => {
    expect(
      latestTranscriptClarifyIsPending(
        [
          {
            content: [
              {
                args: { question: 'Approve?' },
                result: { user_response: 'Yes' },
                toolName: 'clarify',
                type: 'tool-call'
              }
            ]
          }
        ],
        'Approve?'
      )
    ).toBe(false)
  })

  it('accepts the matching latest unanswered transcript question', () => {
    expect(
      latestTranscriptClarifyIsPending(
        [{ content: [{ args: '{"question":"Approve?"}', toolName: 'clarify', type: 'tool-call' }] }],
        'Approve?'
      )
    ).toBe(true)
  })

  it('accepts a new live gateway question before it reaches the transcript', () => {
    expect(latestTranscriptClarifyIsPending([], 'Approve?')).toBe(true)
  })
})
