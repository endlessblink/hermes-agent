import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AssistantState } from '@/store/personal-assistant'

const patchPersonalAssistantState = vi.fn(async () => undefined)
const acknowledgePersonalAssistantRead = vi.fn(async () => undefined)
const $threadScrolledUp = atom(false)

const baseState: AssistantState = {
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
}

const $personalAssistantState = atom(baseState)

vi.mock('@/store/personal-assistant', () => ({
  $personalAssistantState,
  acknowledgePersonalAssistantRead,
  patchPersonalAssistantState
}))
vi.mock('@/store/thread-scroll', () => ({ $threadScrolledUp }))

const { PersonalAssistantSituation } = await import('./personal-assistant-situation')

beforeEach(() => {
  acknowledgePersonalAssistantRead.mockClear()
  $personalAssistantState.set(baseState)
  $threadScrolledUp.set(false)
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
})
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

  it('uses unread activity for the header badge without treating pending proposals as unread', async () => {
    const proposals = [
      { id: 'proposal-1', title: 'First proposal' },
      { id: 'proposal-2', title: 'Second proposal' },
      { id: 'proposal-3', title: 'Third proposal' }
    ]

    $personalAssistantState.set({
      ...baseState,
      captureProposals: proposals,
      pendingApprovals: [],
      unreadCount: 2
    })

    render(<PersonalAssistantSituation />)

    expect(screen.getByText('0 approvals · 3 proposals')).toBeTruthy()
    expect(screen.getByLabelText('2 unread personal assistant updates')).toBeTruthy()

    $personalAssistantState.set({ ...baseState, captureProposals: proposals, pendingApprovals: [], unreadCount: 0 })

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Situation/ }).textContent).toBe('Situationfresh')
      expect(screen.queryByLabelText(/unread personal assistant updates/i)).toBeNull()
    })
  })

  it('acknowledges unread activity when the open assistant is visible at the bottom', async () => {
    render(<PersonalAssistantSituation />)

    expect(screen.getByLabelText('1 unread personal assistant update')).toBeTruthy()
    await waitFor(() => expect(acknowledgePersonalAssistantRead).toHaveBeenCalledTimes(1))

    $personalAssistantState.set({ ...baseState, unreadCount: 2, version: 3 })

    await waitFor(() => expect(acknowledgePersonalAssistantRead).toHaveBeenCalledTimes(2))
  })

  it('waits to acknowledge until the assistant is visible and scrolled to the bottom', async () => {
    $threadScrolledUp.set(true)
    render(<PersonalAssistantSituation />)

    await Promise.resolve()
    expect(acknowledgePersonalAssistantRead).not.toHaveBeenCalled()

    $threadScrolledUp.set(false)
    await waitFor(() => expect(acknowledgePersonalAssistantRead).toHaveBeenCalledTimes(1))
  })

  it('acknowledges unread activity when a bottomed assistant window becomes visible', async () => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })
    render(<PersonalAssistantSituation />)

    await Promise.resolve()
    expect(acknowledgePersonalAssistantRead).not.toHaveBeenCalled()

    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
    document.dispatchEvent(new Event('visibilitychange'))

    await waitFor(() => expect(acknowledgePersonalAssistantRead).toHaveBeenCalledTimes(1))
  })

  it('resolves Hebrew user content independently from the English dashboard chrome', () => {
    $personalAssistantState.set({
      ...baseState,
      outcomes: [{ id: 'outcome-he', title: 'לסיים את תכנון השבוע' }],
      pendingApprovals: [{ id: 'approval-he', title: 'להכין לוח שנה לאירועים' }]
    })

    render(<PersonalAssistantSituation />)

    const outcome = screen.getByText('לסיים את תכנון השבוע')
    const pending = screen.getByText('להכין לוח שנה לאירועים')

    expect(screen.getByText('Situation').closest('aside')?.getAttribute('dir')).toBe('ltr')
    expect(outcome.getAttribute('dir')).toBe('auto')
    expect(outcome.className).toContain('text-start')
    expect(pending.closest('li')?.getAttribute('dir')).toBe('auto')

    fireEvent.click(screen.getByRole('button', { name: 'Edit לסיים את תכנון השבוע' }))
    expect(screen.getByRole('textbox', { name: 'Edit לסיים את תכנון השבוע' }).getAttribute('dir')).toBe('auto')
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
