import { describe, expect, it } from 'vitest'

import type { ChatMessage } from '@/lib/chat-messages'

import { visibleMessagesForRuntime } from './runtime-repository'

describe('visibleMessagesForRuntime', () => {
  it('never gives hidden control prompts to the visible transcript repository', () => {
    const messages: ChatMessage[] = [
      {
        hidden: true,
        id: 'discipline',
        parts: [{ text: '# Suggestion discipline\ninternal rules', type: 'text' }],
        role: 'user'
      },
      {
        id: 'question',
        parts: [{ text: 'כמה אנרגיה יש לך?', type: 'text' }],
        role: 'assistant'
      }
    ]

    expect(visibleMessagesForRuntime(messages).map(message => message.id)).toEqual(['question'])
  })
})
