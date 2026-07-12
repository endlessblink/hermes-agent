import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { afterEach, describe, expect, it, vi } from 'vitest'

const patchPersonalAssistantState = vi.fn(async () => undefined)

const $personalAssistantState = atom({
  schemaVersion: 1 as const,
  version: 2,
  sessionId: 'assistant-home',
  outcomes: [{ id: 'outcome-1', title: 'Ship the launch' }],
  commitments: [],
  capacity: { summary: 'Three focused hours', updatedAt: null },
  focus: { id: 'focus-1', title: 'Launch review' },
  blockers: [{ id: 'blocker-1', title: 'Waiting for approval' }],
  deferred: [],
  pendingApprovals: [{ id: 'approval-1', title: 'Move the deadline' }],
  captureProposals: [],
  sync: { status: 'fresh' as const, lastCheckedAt: null, lastVerifiedAt: '2026-07-12T09:00:00Z' },
  unreadCount: 1,
  episodes: []
})

vi.mock('@/store/personal-assistant', () => ({ $personalAssistantState, patchPersonalAssistantState }))

const { PersonalAssistantSituation } = await import('./personal-assistant-situation')

afterEach(cleanup)

describe('PersonalAssistantSituation', () => {
  it('shows the complete live situation and pending count', () => {
    render(<PersonalAssistantSituation />)

    expect(screen.getByText('Ship the launch')).toBeTruthy()
    expect(screen.getByText('Three focused hours')).toBeTruthy()
    expect(screen.getByText('Waiting for approval')).toBeTruthy()
    expect(screen.getByText('1 approvals · 0 proposals')).toBeTruthy()
    expect(screen.getByText('fresh')).toBeTruthy()
  })

  it('edits an item through a versioned state operation', async () => {
    render(<PersonalAssistantSituation />)

    fireEvent.click(screen.getByRole('button', { name: 'Edit Ship the launch' }))
    const input = screen.getByRole('textbox', { name: 'Edit Ship the launch' })
    fireEvent.change(input, { target: { value: 'Ship safely' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(patchPersonalAssistantState).toHaveBeenCalledWith([
      expect.objectContaining({ id: 'outcome-1', op: 'upsert', section: 'outcomes' })
    ])
  })

  it('keeps a failed state change visible', async () => {
    patchPersonalAssistantState.mockRejectedValueOnce(new Error('State changed elsewhere'))
    render(<PersonalAssistantSituation />)

    fireEvent.click(screen.getByRole('button', { name: 'Archive Waiting for approval' }))

    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('State changed elsewhere'))
  })
})
