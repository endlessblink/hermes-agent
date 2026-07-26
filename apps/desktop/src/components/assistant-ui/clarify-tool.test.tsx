import type { ToolCallMessagePartProps } from '@assistant-ui/react'
import { cleanup, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

import { clarifyRecoveryMessage, clarifyRequestGateway, ClarifyTool, readClarifyResult } from './clarify-tool'

afterEach(() => {
  cleanup()
})

function renderClarify(ui: ReactNode) {
  return render(
    <I18nProvider configClient={null} initialLocale="en">
      {ui}
    </I18nProvider>
  )
}

function settledClarifyProps(
  args: ToolCallMessagePartProps['args'],
  result: ToolCallMessagePartProps['result'],
  toolCallId: string
): ToolCallMessagePartProps {
  return {
    addResult: vi.fn(),
    args,
    argsText: JSON.stringify(args),
    isError: false,
    respondToApproval: vi.fn(),
    result,
    resume: vi.fn(),
    status: { type: 'complete' },
    toolCallId,
    toolName: 'clarify',
    type: 'tool-call'
  }
}

describe('readClarifyResult', () => {
  it('reads question + user_response from the tool JSON payload', () => {
    expect(
      readClarifyResult({
        question: 'Which target?',
        choices_offered: ['staging', 'prod'],
        user_response: 'staging'
      })
    ).toEqual({
      question: 'Which target?',
      answer: 'staging',
      error: undefined
    })
  })

  it('parses a JSON string result the same way as an object', () => {
    expect(
      readClarifyResult(
        JSON.stringify({
          question: 'Ship it?',
          user_response: 'yes'
        })
      )
    ).toEqual({
      question: 'Ship it?',
      answer: 'yes',
      error: undefined
    })
  })

  it('keeps an empty user_response so Skip can render as skipped', () => {
    expect(readClarifyResult({ question: 'Ok?', user_response: '' })).toEqual({
      question: 'Ok?',
      answer: '',
      error: undefined
    })
  })
})

describe('clarifyRecoveryMessage', () => {
  it('continues an interrupted question without asking it again', () => {
    expect(clarifyRecoveryMessage('לאיזו שעה להזיז?', 'מחר ב־10:45')).toBe(
      'תשובה לשאלה ״לאיזו שעה להזיז?״: מחר ב־10:45. המשך מאותה נקודה ואל תשאל שוב את אותה שאלה.'
    )
  })

  it('turns an empty recovery answer into an explicit skip', () => {
    expect(clarifyRecoveryMessage('להמשיך?', '')).toContain(': דלג.')
  })
})

describe('clarifyRequestGateway', () => {
  it('routes the answer through the profile that raised the request', async () => {
    const fallback = vi.fn()
    const request = vi.fn(async () => ({ ok: true }))
    const resolveProfileGateway = vi.fn(async () => ({ request }))
    const send = clarifyRequestGateway('office-work', fallback, resolveProfileGateway as never)

    await send!('clarify.respond', { answer: 'כן', request_id: 'request-1' })

    expect(resolveProfileGateway).toHaveBeenCalledWith('office-work')
    expect(request).toHaveBeenCalledWith('clarify.respond', {
      answer: 'כן',
      request_id: 'request-1'
    })
    expect(fallback).not.toHaveBeenCalled()
  })
})

describe('ClarifyTool settled view', () => {
  it('keeps the question and answer visible after the tool completes', () => {
    renderClarify(
      <ClarifyTool
        {...settledClarifyProps(
          { question: 'Which deployment target?', choices: ['staging', 'prod'] },
          {
            question: 'Which deployment target?',
            choices_offered: ['staging', 'prod'],
            user_response: 'staging'
          },
          'clarify-1'
        )}
      />
    )

    expect(screen.getByText('Which deployment target?')).toBeTruthy()
    expect(screen.getByText('staging')).toBeTruthy()
    expect(document.querySelector('[data-clarify-settled]')).toBeTruthy()
    expect(document.querySelector('[data-clarify-answer]')?.textContent).toBe('staging')
  })

  it('labels an empty response as Skipped', () => {
    renderClarify(
      <ClarifyTool
        {...settledClarifyProps(
          { question: 'Anything else?' },
          { question: 'Anything else?', user_response: '' },
          'clarify-2'
        )}
      />
    )

    expect(screen.getByText('Anything else?')).toBeTruthy()
    expect(screen.getByText('Skipped')).toBeTruthy()
  })
})
