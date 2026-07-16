import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { HermesUiMutationPreviewArtifact, HermesUiTaskBreakdownArtifact } from '@/lib/hermes-ui-artifacts'

import { MutationPreviewCard, TaskBreakdownCard } from './hermes-ui-artifact'
import { RichCodeBlock } from './registry'

const { requestComposerSubmit } = vi.hoisted(() => ({
  requestComposerSubmit: vi.fn((_text: string, _options?: unknown) => true)
}))

vi.mock('@/app/chat/composer/focus', () => ({ requestComposerSubmit }))

const breakdown: HermesUiTaskBreakdownArtifact = {
  direction: 'ltr',
  proposalId: 'proposal-vague',
  proposalRevision: 2,
  schemaVersion: 1,
  scope: 'working-session',
  steps: [
    { clientId: 'discover', doneEnough: 'The missing input is named', estimateMinutes: 10, title: 'Find what is missing' },
    { clientId: 'draft', doneEnough: 'A rough draft exists', estimateMinutes: 20, title: 'Make a rough draft' }
  ],
  submitLabel: 'Update breakdown',
  task: { baseRevision: 11, id: 'task-vague', title: 'Move the site forward' },
  title: 'Editable breakdown',
  type: 'task-breakdown'
}

function preview(expiresAt = '2099-07-16T12:00:00Z'): HermesUiMutationPreviewArtifact {
  return {
    canonicalApproval: {
      action: 'subtask_batch',
      baseRevision: 11,
      contractVersion: 'task-v1',
      operationId: 'breakdown:proposal-vague:r2',
      operations: [{ clientId: 'discover', kind: 'create', order: 0, title: 'Find what is missing' }],
      previewDigest: 'a'.repeat(64),
      previewExpiresAt: expiresAt,
      proposalId: 'proposal-vague',
      proposalRevision: 2,
      requestHash: 'b'.repeat(64),
      taskId: 'task-vague'
    },
    changes: [{ after: { steps: 1 }, before: { steps: 0 }, operation: 'update', taskId: 'task-vague', title: 'Move the site forward' }],
    direction: 'ltr',
    title: 'Approve exact breakdown',
    type: 'mutation-preview'
  }
}

beforeEach(() => {
  localStorage.clear()
  requestComposerSubmit.mockClear()
  requestComposerSubmit.mockReturnValue(true)
})

afterEach(cleanup)

describe('TaskBreakdownCard', () => {
  it('keeps stable row identity while editing and reordering, then submits a revision rather than approval', async () => {
    render(<TaskBreakdownCard artifact={breakdown} />)

    fireEvent.change(screen.getByDisplayValue('Find what is missing'), { target: { value: 'Name the missing input' } })
    fireEvent.click(screen.getByRole('button', { name: 'Move down Name the missing input' }))
    fireEvent.click(screen.getByRole('button', { name: 'Update breakdown' }))

    await waitFor(() => expect(requestComposerSubmit).toHaveBeenCalledTimes(1))
    const [text, options] = requestComposerSubmit.mock.calls[0] as unknown as [string, Record<string, unknown>]
    const decision = JSON.parse(text.slice(text.indexOf('\n') + 1))

    expect(decision).toMatchObject({ approval: false, proposalId: 'proposal-vague', type: 'task-breakdown-revision' })
    expect(decision.steps.map((step: { clientId: string }) => step.clientId)).toEqual(['draft', 'discover'])
    expect(options).toEqual({ flowstateDecision: decision, hidden: true, target: 'main' })
  })

  it('persists edits only for the exact proposal and canonical task revision', () => {
    const { unmount } = render(<TaskBreakdownCard artifact={breakdown} />)
    fireEvent.change(screen.getByDisplayValue('Find what is missing'), { target: { value: 'Saved local edit' } })
    unmount()

    render(<TaskBreakdownCard artifact={{ ...breakdown, task: { ...breakdown.task, baseRevision: 12 } }} />)
    expect(screen.queryByDisplayValue('Saved local edit')).toBeNull()
    expect(screen.getByDisplayValue('Find what is missing')).toBeTruthy()
  })

  it('keeps generated identities stable while adding and removing editable steps', async () => {
    render(<TaskBreakdownCard artifact={breakdown} />)

    fireEvent.click(screen.getByRole('button', { name: 'Add step' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Step 3' }), {
      target: { value: 'Share the rough draft' }
    })
    fireEvent.change(screen.getByRole('textbox', { name: 'Done enough 3' }), {
      target: { value: 'The draft has one reviewer' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Remove Make a rough draft' }))
    fireEvent.click(screen.getByRole('button', { name: 'Update breakdown' }))

    await waitFor(() => expect(requestComposerSubmit).toHaveBeenCalledTimes(1))
    const text = requestComposerSubmit.mock.calls[0]?.[0] as string
    const decision = JSON.parse(text.slice(text.indexOf('\n') + 1))

    expect(decision.steps.map((step: { clientId: string }) => step.clientId)).toEqual([
      'discover',
      'proposal-vague-new-1'
    ])
  })
})

describe('MutationPreviewCard', () => {
  it('shows every exact canonical field and ignores the model-authored change summary', () => {
    const artifact = preview()
    artifact.changes = [{
      operation: 'delete',
      risk: 'high',
      taskId: 'task-vague',
      title: 'MISLEADING MODEL SUMMARY'
    }]
    artifact.canonicalApproval.operations = [
      {
        canvasPosition: { x: 12, y: 24 },
        clientId: 'new-step',
        completedPomodoros: 2,
        description: 'Exact description',
        doneEnough: 'Exact stopping condition',
        estimateMinutes: 35,
        isCompleted: false,
        kind: 'create',
        order: 4,
        title: 'Exact canonical title'
      },
      { kind: 'delete', subtaskId: 'old-step' }
    ]

    render(<MutationPreviewCard artifact={artifact} />)

    expect(screen.queryByText('MISLEADING MODEL SUMMARY')).toBeNull()
    const createText = document.querySelector('[data-exact-operation="create"]')?.textContent || ''
    const deleteText = document.querySelector('[data-exact-operation="delete"]')?.textContent || ''

    expect(createText).toContain('Create subtask')
    expect(createText).toContain('Exact canonical title')
    expect(createText).toContain('Exact description')
    expect(createText).toContain('Exact stopping condition')
    expect(createText).toContain('Estimate minutes35')
    expect(createText).toContain('Completed focus sessions2')
    expect(createText).toContain('x: 12, y: 24')
    expect(createText).toContain('CompletedNo')
    expect(createText).toContain('Order4')
    expect(createText).toContain('New subtask identitynew-step')
    expect(deleteText).toContain('Delete subtask')
    expect(deleteText).toContain('Subtask identityold-step')
  })

  it('submits the exact canonical proof once through the hidden decision channel', async () => {
    render(<MutationPreviewCard artifact={preview()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Approve exact changes' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Approved and sent' })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Approved and sent' }))

    expect(requestComposerSubmit).toHaveBeenCalledTimes(1)
    const [text, options] = requestComposerSubmit.mock.calls[0] as unknown as [string, Record<string, unknown>]
    const decision = JSON.parse(text.slice(text.indexOf('\n') + 1))
    expect(decision).toMatchObject({ approval: true, decision: 'approve', previewDigest: 'a'.repeat(64) })
    expect(options).toEqual({ flowstateDecision: decision, hidden: true, target: 'main' })
  })

  it('treats correction as revision, never approval, and disables expired proof', async () => {
    const { rerender } = render(<MutationPreviewCard artifact={preview()} />)

    fireEvent.change(screen.getByRole('textbox', { name: 'Correction or missing context' }), {
      target: { value: 'Keep the research step optional.' }
    })
    expect(screen.getByRole('button', { name: 'Approve exact changes' }).hasAttribute('disabled')).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Request a new preview' }))

    await waitFor(() => expect(requestComposerSubmit).toHaveBeenCalledTimes(1))
    const text = requestComposerSubmit.mock.calls[0]?.[0] as string
    expect(JSON.parse(text.slice(text.indexOf('\n') + 1))).toMatchObject({
      approval: false,
      correction: 'Keep the research step optional.',
      decision: 'revise'
    })

    rerender(<MutationPreviewCard artifact={preview('2020-01-01T00:00:00Z')} />)
    expect(screen.getByText('This preview expired. Request a new preview.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Approve exact changes' }).hasAttribute('disabled')).toBe(true)
  })

  it('becomes retryable after a rejected submit and resets for a new proof', async () => {
    requestComposerSubmit.mockReturnValueOnce(false).mockReturnValueOnce(true)
    const { rerender } = render(<MutationPreviewCard artifact={preview()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Approve exact changes' }))
    await waitFor(() => expect(screen.getByText('Send failed. You can retry.')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Approve exact changes' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Approved and sent' })).toBeTruthy())

    const next = preview()
    next.canonicalApproval = {
      ...next.canonicalApproval,
      operationId: 'breakdown:proposal-vague:r3',
      previewDigest: 'c'.repeat(64),
      proposalRevision: 3,
      requestHash: 'd'.repeat(64)
    }
    rerender(<MutationPreviewCard artifact={next} />)

    await waitFor(() => expect(screen.getByRole('button', { name: 'Approve exact changes' })).toBeTruthy())
    expect(requestComposerSubmit).toHaveBeenCalledTimes(2)
  })
})

describe('hermes-ui rich fences', () => {
  it('replaces a valid completed fence with its card without flashing raw JSON', async () => {
    render(<RichCodeBlock code={JSON.stringify(breakdown)} fallback={<pre>raw valid JSON</pre>} language="hermes-ui" />)

    await waitFor(() => expect(document.querySelector('[data-hermes-ui-artifact="task-breakdown"]')).toBeTruthy())
    expect(screen.queryByText('raw valid JSON')).toBeNull()
  })

  it('shows a placeholder for incomplete streaming JSON and never exposes raw JSON', () => {
    render(<RichCodeBlock code={'{"type":"task-breakdown","steps":['} fallback={<pre>raw partial JSON</pre>} language="hermes-ui" streaming />)

    expect(screen.getByRole('status').textContent).toBe('Preparing interactive assistant…')
    expect(screen.queryByText('raw partial JSON')).toBeNull()
  })

  it('shows a bounded resend notice for an invalid completed fence and never exposes raw JSON', () => {
    render(<RichCodeBlock code="{ nope" fallback={<pre>raw invalid JSON</pre>} language="hermes-ui" />)

    expect(screen.getByRole('alert').textContent).toContain('Interactive assistant could not be shown')
    expect(screen.queryByText('raw invalid JSON')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Ask Hermes to resend' }))
    expect(requestComposerSubmit).toHaveBeenCalledWith(
      expect.stringContaining('one complete valid hermes-ui artifact'),
      { target: 'main' }
    )
  })
})
