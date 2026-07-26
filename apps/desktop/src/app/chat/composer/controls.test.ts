import { describe, expect, it } from 'vitest'

import {
  isComposerPrimaryActionDisabled,
  resolveClarifyGateway,
  resolveComposerBusyAction,
  shouldShowClarifyRecovery
} from './controls'

describe('isComposerPrimaryActionDisabled', () => {
  it('keeps the queue action enabled while the agent is busy', () => {
    expect(
      isComposerPrimaryActionDisabled({
        busy: true,
        canSubmit: false,
        disabled: false
      })
    ).toBe(false)
  })

  it('disables an ordinary send when the draft cannot be submitted', () => {
    expect(
      isComposerPrimaryActionDisabled({
        busy: false,
        canSubmit: false,
        disabled: false
      })
    ).toBe(true)
  })

  it('honors the global disabled state while busy', () => {
    expect(
      isComposerPrimaryActionDisabled({
        busy: true,
        canSubmit: true,
        disabled: true
      })
    ).toBe(true)
  })
})

describe('resolveComposerBusyAction', () => {
  it('shows answer rather than queue while a clarification is active', () => {
    expect(
      resolveComposerBusyAction({
        answersClarify: true,
        hasComposerPayload: true,
        sendBlocked: true
      })
    ).toBe('answer')
  })

  it('keeps ordinary busy text on the queue', () => {
    expect(
      resolveComposerBusyAction({
        answersClarify: false,
        hasComposerPayload: true,
        sendBlocked: true
      })
    ).toBe('queue')
  })
})

describe('shouldShowClarifyRecovery', () => {
  it('shows a restored question after the transient tool row settles', () => {
    expect(shouldShowClarifyRecovery({ busy: false, hasRequest: true })).toBe(true)
    expect(shouldShowClarifyRecovery({ busy: true, hasRequest: true })).toBe(false)
    expect(shouldShowClarifyRecovery({ busy: false, hasRequest: false })).toBe(false)
  })
})

describe('resolveClarifyGateway', () => {
  it('uses the active profile gateway when the composer primary is disconnected', () => {
    const active = { name: 'office-work' }

    expect(resolveClarifyGateway(null, active)).toBe(active)
  })
})
