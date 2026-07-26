import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { useRef } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { requestComposerSubmit } from '../focus'
import { ComposerScopeProvider, MAIN_COMPOSER_SCOPE } from '../scope'

import { useComposerSubmit } from './use-composer-submit'

vi.mock('@/lib/haptics', () => ({ triggerHaptic: () => {} }))
vi.mock('@/lib/desktop-diagnostics', () => ({ emitDesktopDiagnostic: vi.fn() }))

afterEach(cleanup)

interface HarnessProps {
  attachments?: unknown[]
  busy?: boolean
  onClarify?: (text: string) => boolean | Promise<boolean | 'stale'>
  onQueue: () => boolean
  onSubmit: (text: string) => boolean | Promise<boolean>
  presentedBusyAction?: 'answer' | 'queue' | 'stop'
  recoverLostClarifyWhileBusy?: boolean
  target?: string
}

function HarnessBody({
  attachments = [],
  busy = false,
  onClarify,
  onQueue,
  onSubmit,
  presentedBusyAction,
  recoverLostClarifyWhileBusy = false
}: HarnessProps) {
  const activeQueueSessionKeyRef = useRef<string | null>('stored-session')
  const draftRef = useRef('')
  const editorRef = useRef<HTMLDivElement | null>(null)

  const { submitDraft } = useComposerSubmit({
    activeQueueSessionKey: 'stored-session',
    activeQueueSessionKeyRef,
    attachments: attachments as never[],
    busy,
    canSteer: false,
    clearDraft: () => {
      draftRef.current = ''

      if (editorRef.current) {
        editorRef.current.textContent = ''
      }
    },
    disabled: false,
    draftRef,
    drainNextQueued: async () => false,
    editorRef,
    exitQueuedEdit: () => false,
    focusInput: () => undefined,
    implicitQueueDrainAllowed: () => false,
    inputDisabled: false,
    loadIntoComposer: text => {
      draftRef.current = text

      if (editorRef.current) {
        editorRef.current.textContent = text
      }
    },
    onCancel: vi.fn(),
    onSteer: undefined,
    onSubmit,
    onSubmitClarifyAnswer: onClarify,
    presentedBusyAction: presentedBusyAction ?? (onClarify ? 'answer' : busy ? 'queue' : 'stop'),
    queueCurrentDraft: onQueue,
    queueEdit: null,
    recoverLostClarifyWhileBusy,
    sendBlocked: busy,
    sessionId: 'runtime-session',
    setComposerText: value => {
      draftRef.current = value
    },
    stashAt: vi.fn()
  })

  return (
    <>
      <div
        contentEditable
        data-testid="editor"
        onInput={event => {
          draftRef.current = event.currentTarget.textContent ?? ''
        }}
        ref={editorRef}
        suppressContentEditableWarning
      />
      <button data-testid="submit" onClick={submitDraft} type="button" />
    </>
  )
}

function Harness({ target = 'main', ...props }: HarnessProps) {
  return (
    <ComposerScopeProvider value={{ ...MAIN_COMPOSER_SCOPE, target }}>
      <HarnessBody {...props} />
    </ComposerScopeProvider>
  )
}

describe('useComposerSubmit clarify routing', () => {
  it('submits an external request only through the composer it explicitly targets', async () => {
    const mainSubmit = vi.fn(() => true)
    const assistantSubmit = vi.fn(() => true)

    render(
      <>
        <Harness onQueue={() => true} onSubmit={mainSubmit} target="main" />
        <Harness onQueue={() => true} onSubmit={assistantSubmit} target="tile:personal-assistant" />
      </>
    )

    requestComposerSubmit('continue the assistant interview', { target: 'tile:personal-assistant' })

    await waitFor(() => expect(assistantSubmit).toHaveBeenCalledWith('continue the assistant interview', undefined))
    expect(mainSubmit).not.toHaveBeenCalled()
  })

  it('reports when the presented queue action actually answers a clarification', async () => {
    const { emitDesktopDiagnostic } = await import('@/lib/desktop-diagnostics')
    const onClarify = vi.fn(async () => true)
    const { getByTestId } = render(
      <Harness
        busy
        onClarify={onClarify}
        onQueue={() => true}
        onSubmit={() => true}
        presentedBusyAction="queue"
      />
    )
    const editor = getByTestId('editor')

    await act(async () => {
      editor.textContent = 'answer now'
      fireEvent.input(editor)
      fireEvent.click(getByTestId('submit'))
    })

    expect(emitDesktopDiagnostic).toHaveBeenCalledWith({
      component: 'composer',
      event: 'queue.action_mismatch',
      severity: 'error',
      message: 'Composer control performed a different action than it presented',
      details: { actual_action: 'answer', presented_action: 'queue' }
    })
  })

  it('does not restore a rejected hidden form response into the composer', async () => {
    const onQueue = vi.fn(() => true)
    const onSubmit = vi.fn(() => false)
    const { getByTestId } = render(<Harness onQueue={onQueue} onSubmit={onSubmit} />)

    requestComposerSubmit('Hermes UI form response:\n{"type":"form-response"}', {
      allowWhileBusy: true,
      hidden: true,
      target: 'main'
    })

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(expect.any(String), { allowWhileBusy: true, hidden: true })
    })
    expect(getByTestId('editor').textContent).toBe('')
  })

  it('answers an active clarify request from the main composer instead of queuing', async () => {
    const onClarify = vi.fn(async () => true)
    const onQueue = vi.fn(() => true)
    const onSubmit = vi.fn(() => true)

    const { getByTestId } = render(<Harness busy onClarify={onClarify} onQueue={onQueue} onSubmit={onSubmit} />)
    const editor = getByTestId('editor')

    await act(async () => {
      editor.textContent = 'כן, תמשיך'
      fireEvent.input(editor)
      fireEvent.click(getByTestId('submit'))
    })

    expect(onClarify).toHaveBeenCalledWith('כן, תמשיך')
    expect(onQueue).not.toHaveBeenCalled()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('keeps ordinary busy submits on the queue when there is no clarify request', async () => {
    const onQueue = vi.fn(() => true)
    const onSubmit = vi.fn(() => true)

    const { getByTestId } = render(<Harness busy onQueue={onQueue} onSubmit={onSubmit} />)
    const editor = getByTestId('editor')

    await act(async () => {
      editor.textContent = 'send after current run'
      fireEvent.input(editor)
      fireEvent.click(getByTestId('submit'))
    })

    expect(onQueue).toHaveBeenCalledTimes(1)
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('reports a rejected queue action so the watchdog can see it', async () => {
    const { emitDesktopDiagnostic } = await import('@/lib/desktop-diagnostics')
    const onQueue = vi.fn(() => false)
    const { getByTestId } = render(<Harness busy onQueue={onQueue} onSubmit={vi.fn(() => true)} />)
    const editor = getByTestId('editor')

    await act(async () => {
      editor.textContent = 'send after current run'
      fireEvent.input(editor)
      fireEvent.click(getByTestId('submit'))
    })

    expect(emitDesktopDiagnostic).toHaveBeenCalledWith({
      component: 'queue',
      event: 'queue.error',
      severity: 'error',
      message: 'Composer could not add the draft to the queue',
      details: { reason: 'enqueue_rejected' }
    })
  })

  it('sends busy Personal Assistant text to the gateway when clarify client state was lost', async () => {
    const onQueue = vi.fn(() => true)
    const onSubmit = vi.fn(() => true)

    const { getByTestId } = render(<Harness busy onQueue={onQueue} onSubmit={onSubmit} recoverLostClarifyWhileBusy />)

    const editor = getByTestId('editor')

    await act(async () => {
      editor.textContent = 'FlowState — continue there'
      fireEvent.input(editor)
      fireEvent.click(getByTestId('submit'))
    })

    expect(onSubmit).toHaveBeenCalledWith('FlowState — continue there', { allowWhileBusy: true })
    expect(onQueue).not.toHaveBeenCalled()
    expect(editor.textContent).toBe('')
  })

  it('does not route attachment submits through clarify answers', async () => {
    const onClarify = vi.fn(async () => true)
    const onQueue = vi.fn(() => true)
    const onSubmit = vi.fn(() => true)

    const { getByTestId } = render(
      <Harness attachments={[{ id: 'file-1' }]} busy onClarify={onClarify} onQueue={onQueue} onSubmit={onSubmit} />
    )

    const editor = getByTestId('editor')

    await act(async () => {
      editor.textContent = 'see attached'
      fireEvent.input(editor)
      fireEvent.click(getByTestId('submit'))
    })

    expect(onClarify).not.toHaveBeenCalled()
    expect(onQueue).toHaveBeenCalledTimes(1)
  })
})

// Regression net for the 2026-07-19 "dead send button" trap: a clarify request
// the gateway no longer tracks must not eat the user's message — the stale
// path re-routes the same text to the queue in the same click.
describe('useComposerSubmit stale clarify fallback', () => {
  it('re-queues the typed text when the clarify request went stale on the gateway', async () => {
    const onQueue = vi.fn(() => true)
    const onClarify = vi.fn(async () => 'stale' as const)
    const { getByTestId } = render(
      <Harness busy onClarify={onClarify} onQueue={onQueue} onSubmit={vi.fn(() => true)} />
    )
    const editor = getByTestId('editor')
    editor.textContent = 'my status update'
    fireEvent.input(editor)

    await act(async () => {
      fireEvent.click(getByTestId('submit'))
    })

    await waitFor(() => expect(onQueue).toHaveBeenCalledTimes(1))
    expect(onClarify).toHaveBeenCalledWith('my status update')
  })
})
