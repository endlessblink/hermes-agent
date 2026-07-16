import { cleanup, render, waitFor } from '@testing-library/react'
import { useRef } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { requestComposerSubmit } from '../focus'

import { useComposerSubmit } from './use-composer-submit'

vi.mock('@/lib/haptics', () => ({ triggerHaptic: () => {} }))

afterEach(cleanup)

function Harness({
  inputDisabled = false,
  onSubmit
}: {
  inputDisabled?: boolean
  onSubmit: (text: string, options?: Record<string, unknown>) => boolean | Promise<boolean>
}) {
  const activeQueueSessionKeyRef = useRef<string | null>('stored-session')
  const draftRef = useRef('')
  const editorRef = useRef<HTMLDivElement | null>(null)

  useComposerSubmit({
    activeQueueSessionKey: 'stored-session',
    activeQueueSessionKeyRef,
    attachments: [],
    busy: false,
    canSteer: false,
    clearDraft: () => undefined,
    disabled: false,
    draftRef,
    drainNextQueued: async () => false,
    editorRef,
    exitQueuedEdit: () => false,
    focusInput: () => undefined,
    inputDisabled,
    loadIntoComposer: () => undefined,
    onCancel: vi.fn(),
    onSteer: undefined,
    onSubmit,
    queueCurrentDraft: () => false,
    queueEdit: null,
    queuedPrompts: [],
    sessionId: 'runtime-session',
    setComposerText: () => undefined,
    stashAt: vi.fn()
  })

  return <div contentEditable data-testid="editor" ref={editorRef} suppressContentEditableWarning />
}

describe('useComposerSubmit external submit acknowledgement', () => {
  it('forwards hidden FlowState decisions and acknowledges gateway acceptance', async () => {
    const decision = { decision: 'approve', proposalId: 'proposal-1' }
    const onSubmit = vi.fn(async () => true)

    render(<Harness onSubmit={onSubmit} />)

    const accepted = requestComposerSubmit('approve exact revision', {
      flowstateDecision: decision,
      hidden: true,
      target: 'main'
    })

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith('approve exact revision', {
        flowstateDecision: decision,
        hidden: true
      })
    })
    expect(await accepted).toBe(true)
  })

  it('acknowledges rejection when the composer cannot accept the request', async () => {
    const onSubmit = vi.fn(async () => true)

    render(<Harness inputDisabled onSubmit={onSubmit} />)

    expect(await requestComposerSubmit('approve', { hidden: true, target: 'main' })).toBe(false)
    expect(onSubmit).not.toHaveBeenCalled()
  })
})
