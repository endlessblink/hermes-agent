import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AssistantState } from '@/store/personal-assistant'

const patchPersonalAssistantState = vi.fn(async () => undefined)
const acknowledgePersonalAssistantRead = vi.fn(async () => undefined)
const refreshPersonalAssistantState = vi.fn(async () => undefined)
const submitPersonalAssistantShadowAction = vi.fn(async () => undefined)
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

function mountBottomedThreadViewport() {
  const viewport = document.createElement('div')

  viewport.dataset.slot = 'aui_thread-viewport'
  viewport.dataset.following = 'true'
  document.body.append(viewport)
}

vi.mock('@/store/personal-assistant', () => ({
  $personalAssistantState,
  acknowledgePersonalAssistantRead,
  patchPersonalAssistantState,
  refreshPersonalAssistantState,
  submitPersonalAssistantShadowAction
}))
vi.mock('@/store/thread-scroll', () => ({ $threadScrolledUp }))

const { PersonalAssistantSituation } = await import('./personal-assistant-situation')

const reviewInChat = vi.fn()

const openChat = vi.fn()

function SituationHarness({
  initiallyOpen = false,
  showAttentionStrip = true
}: {
  initiallyOpen?: boolean
  showAttentionStrip?: boolean
}) {
  const [open, setOpen] = useState(initiallyOpen)

  return (
    <PersonalAssistantSituation
      onOpenChange={setOpen}
      onOpenChat={openChat}
      onReviewInChat={reviewInChat}
      open={open}
      showAttentionStrip={showAttentionStrip}
    />
  )
}

function renderSituation(initiallyOpen = false) {
  return render(<SituationHarness initiallyOpen={initiallyOpen} />)
}

function expandSituation() {
  fireEvent.click(screen.getByRole('button', { name: 'Review assistant context' }))
}

beforeEach(() => {
  acknowledgePersonalAssistantRead.mockClear()
  refreshPersonalAssistantState.mockClear()
  patchPersonalAssistantState.mockClear()
  submitPersonalAssistantShadowAction.mockClear()
  reviewInChat.mockClear()
  openChat.mockClear()
  $personalAssistantState.set(baseState)
  $threadScrolledUp.set(false)
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
})
afterEach(() => {
  cleanup()
  document.querySelectorAll('[data-slot="aui_thread-viewport"]').forEach(viewport => viewport.remove())
})

describe('PersonalAssistantSituation', () => {
  it('shows whether the watchdog is actually observing and repairing failures', () => {
    $personalAssistantState.set({
      ...baseState,
      watchdog: {
        state: 'active',
        heartbeatAt: '2026-07-21T07:20:16Z',
        startedAt: '2026-07-21T07:00:00Z',
        watchedSources: 12,
        latestEvent: 'tool_failure',
        latestAt: '2026-07-21T07:18:00Z',
        latestSeverity: 'error',
        latestTool: 'personal_assistant_interview_start',
        repairStatus: 'queued',
        repairTaskId: 'repair-123'
      }
    })

    renderSituation(true)

    expect(screen.getByText('Watchdog active')).toBeTruthy()
    expect(screen.getByText('12 signals monitored')).toBeTruthy()
    expect(screen.getByText('Tool failure')).toBeTruthy()
    expect(screen.getByText('Repair queued')).toBeTruthy()
  })

  it('never presents a verified candidate as an applied repair', () => {
    $personalAssistantState.set({
      ...baseState,
      watchdog: {
        state: 'active',
        heartbeatAt: '2026-07-21T09:00:00Z',
        startedAt: '2026-07-21T07:00:00Z',
        watchedSources: 12,
        latestEvent: 'composer_action_mismatch',
        latestAt: '2026-07-21T08:58:00Z',
        latestSeverity: 'error',
        latestTool: null,
        repairStatus: 'candidate_ready',
        repairTaskId: 'repair-456',
        repairUpdatedAt: '2026-07-21T09:00:00Z',
        repairOutcomeCode: 'verification_passed'
      }
    })

    renderSituation(true)

    expect(screen.getByText('Tested candidate ready — not applied')).toBeTruthy()
    expect(screen.getByText('Task repair-456 · verification passed')).toBeTruthy()
    expect(document.querySelector('time[datetime="2026-07-21T09:00:00Z"]')).toBeTruthy()
    expect(screen.queryByText(/recovered/i)).toBeNull()
  })

  it('keeps the dashboard out of the transcript and opens details on demand', () => {
    renderSituation()

    const toggle = screen.getByRole('button', { name: 'Review assistant context' })

    expect(screen.getByText('1 decision waiting')).toBeTruthy()
    expect(screen.queryByText('Ship the launch')).toBeNull()

    fireEvent.click(toggle)

    expect(screen.getByRole('dialog', { name: 'Assistant context' })).toBeTruthy()
    expect(screen.getByText('Ship the launch')).toBeTruthy()
  })

  it('hides the attention strip when nothing needs attention', () => {
    $personalAssistantState.set({
      ...baseState,
      blockers: [],
      pendingApprovals: [],
      captureProposals: [],
      protectedItems: [],
      latestCoverageReceipt: undefined,
      unreadCount: 0
    })

    renderSituation(true)

    expect(screen.queryByRole('button', { name: 'Review assistant context' })).toBeNull()
    expect(screen.getByRole('dialog', { name: 'Assistant context' })).toBeTruthy()
  })

  it('can be opened globally without adding its attention strip to another chat', () => {
    render(<SituationHarness initiallyOpen showAttentionStrip={false} />)

    expect(screen.queryByRole('button', { name: 'Review assistant context' })).toBeNull()
    expect(screen.getByRole('dialog', { name: 'Assistant context' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Open assistant chat' }))
    expect(openChat).toHaveBeenCalledTimes(1)
  })

  it('shows the complete live situation and pending count', () => {
    renderSituation()
    expandSituation()

    expect(screen.getByText('Ship the launch')).toBeTruthy()
    expect(screen.getByText('Three focused hours')).toBeTruthy()
    expect(screen.getByText('Waiting for approval')).toBeTruthy()
    expect(screen.getByText('1 approval · 0 proposals')).toBeTruthy()
    expect(screen.getByText('fresh')).toBeTruthy()
  })

  it('shows the active plan as task names and applies its direct action', async () => {
    $personalAssistantState.set({
      ...baseState,
      activeTurn: {
        cardRevision: 2,
        outcome: {
          cardRevision: 2,
          kind: 'plan',
          options: [{ actionId: 'include:task-one', reason: 'Fits the available hour', taskName: 'Finish the proposal' }]
        },
        phase: 'awaiting-action',
        revision: 3,
        submissionId: 'turn-1'
      }
    })

    renderSituation(true)

    expect(screen.getByText('Plan ready')).toBeTruthy()
    expect(screen.getByText('Finish the proposal')).toBeTruthy()
    expect(screen.getByText('Fits the available hour')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Choose Finish the proposal' }))

    await waitFor(() => expect(submitPersonalAssistantShadowAction).toHaveBeenCalledWith('include:task-one', 2))
  })

  it('submits the progress answer directly to the active assistant turn', async () => {
    $personalAssistantState.set({
      ...baseState,
      activeTurn: {
        cardRevision: 1,
        outcome: { cardRevision: 1, kind: 'progress-question', questionId: 'progressReview' },
        phase: 'awaiting-context',
        revision: 2,
        submissionId: 'turn-1'
      }
    })

    renderSituation(true)

    expect(screen.getByText('What have you completed since we last checked?')).toBeTruthy()
    fireEvent.change(screen.getByRole('textbox', { name: 'Progress since last check' }), {
      target: { value: 'I finished the release notes.' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Continue planning' }))

    await waitFor(() =>
      expect(submitPersonalAssistantShadowAction).toHaveBeenCalledWith('answer-progress', 1, {
        progressReview: 'I finished the release notes.'
      })
    )
  })

  it('renders recovery outcomes without claiming a focused next step', () => {
    $personalAssistantState.set({
      ...baseState,
      activeTurn: {
        cardRevision: 3,
        outcome: { cardRevision: 3, kind: 'recovery', message: 'The plan needs to be refreshed.' },
        phase: 'recoverable-failure',
        revision: 4,
        submissionId: 'turn-1'
      }
    })

    renderSituation()

    expect(screen.getByText('Assistant needs review')).toBeTruthy()
    expandSituation()
    expect(screen.getByText('The plan needs to be refreshed.')).toBeTruthy()
  })

  it('shows whether the protected safety sweep is complete and actionable', () => {
    $personalAssistantState.set({
      ...baseState,
      protectedItems: [{ id: 'flowstate:health', title: 'Arrange the required blood test', disposition: 'actionable' }],
      latestCoverageReceipt: {
        id: 'receipt-1',
        cadence: 'daily',
        expectedItemIds: ['flowstate:health'],
        reviewedItemIds: ['flowstate:health'],
        missingItemIds: [],
        riskItemIds: ['flowstate:health'],
        unresolvedItemIds: [],
        blockingReasons: [],
        complete: true,
        allClear: false,
        createdAt: '2026-07-19T09:00:00Z'
      }
    })

    renderSituation()
    expandSituation()

    expect(screen.getByText('1 protected item checked')).toBeTruthy()
    expect(screen.getByText('1 needs attention')).toBeTruthy()
  })

  it('moves read-only safety review into the composer instead of acting automatically', () => {
    $personalAssistantState.set({
      ...baseState,
      protectedItems: [{ id: 'flowstate:health', title: 'Arrange the required blood test', disposition: 'actionable' }],
      latestCoverageReceipt: {
        id: 'receipt-1',
        cadence: 'daily',
        expectedItemIds: ['flowstate:health'],
        reviewedItemIds: ['flowstate:health'],
        missingItemIds: [],
        riskItemIds: ['flowstate:health'],
        unresolvedItemIds: [],
        blockingReasons: [],
        complete: true,
        allClear: false,
        createdAt: '2026-07-19T09:00:00Z'
      }
    })

    renderSituation()
    expandSituation()
    fireEvent.click(screen.getByRole('button', { name: 'Review safety issues in chat' }))

    expect(reviewInChat).toHaveBeenCalledWith(
      'Review the unresolved protected items and help me decide the next action for each one.'
    )
    expect(screen.queryByRole('dialog', { name: 'Assistant context' })).toBeNull()
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

    renderSituation()
    expandSituation()

    expect(screen.getByText('0 approvals · 3 proposals')).toBeTruthy()
    expect(screen.getByLabelText('2 unread personal assistant updates')).toBeTruthy()

    $personalAssistantState.set({ ...baseState, captureProposals: proposals, pendingApprovals: [], unreadCount: 0 })

    await waitFor(() => expect(screen.queryByLabelText(/unread personal assistant updates/i)).toBeNull())
  })

  it('lets the user edit, accept, or reject learned context proposals', async () => {
    const proposal = {
      id: 'proposal-1',
      title: 'Keep weekly plans compact',
      section: 'preferences',
      status: 'pending'
    }

    $personalAssistantState.set({ ...baseState, captureProposals: [proposal], unreadCount: 0 })

    renderSituation()
    expandSituation()

    fireEvent.click(screen.getByRole('button', { name: 'Edit Keep weekly plans compact' }))
    fireEvent.change(screen.getByDisplayValue('Keep weekly plans compact'), {
      target: { value: 'Discuss the week before drafting it' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Accept learned preference' }))

    await waitFor(() =>
      expect(patchPersonalAssistantState).toHaveBeenCalledWith([
        {
          id: 'proposal-1',
          op: 'upsert',
          section: 'preferences',
          value: expect.objectContaining({ title: 'Discuss the week before drafting it' })
        },
        {
          id: 'proposal-1',
          op: 'upsert',
          section: 'captureProposals',
          value: expect.objectContaining({ status: 'accepted' })
        }
      ])
    )

    fireEvent.click(screen.getByRole('button', { name: 'Reject learned preference' }))
    await waitFor(() =>
      expect(patchPersonalAssistantState).toHaveBeenCalledWith([
        {
          id: 'proposal-1',
          op: 'upsert',
          section: 'captureProposals',
          value: expect.objectContaining({ status: 'rejected' })
        }
      ])
    )
  })

  it('reviews a large proposal queue one decision at a time', () => {
    const proposals = Array.from({ length: 24 }, (_, index) => ({
      id: `proposal-${index + 1}`,
      section: 'preferences',
      status: 'pending',
      title: `Proposal ${index + 1}`
    }))

    $personalAssistantState.set({
      ...baseState,
      blockers: [],
      captureProposals: proposals,
      pendingApprovals: [],
      unreadCount: 0
    })

    renderSituation()
    expandSituation()

    expect(screen.getByText('1 of 24')).toBeTruthy()
    expect(screen.getByText('Proposal 1')).toBeTruthy()
    expect(screen.queryByText('Proposal 2')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Next decision' }))

    expect(screen.getByText('2 of 24')).toBeTruthy()
    expect(screen.getByText('Proposal 2')).toBeTruthy()
    expect(screen.queryByText('Proposal 1')).toBeNull()
  })

  it('acknowledges unread activity when the open assistant is visible at the bottom', async () => {
    mountBottomedThreadViewport()
    renderSituation()
    expandSituation()

    expect(screen.getByLabelText('1 unread personal assistant update')).toBeTruthy()
    await waitFor(() => expect(acknowledgePersonalAssistantRead).toHaveBeenCalledTimes(1))

    $personalAssistantState.set({ ...baseState, unreadCount: 2, version: 3 })

    await waitFor(() => expect(acknowledgePersonalAssistantRead).toHaveBeenCalledTimes(2))

    $personalAssistantState.set({ ...baseState, unreadCount: 2, version: 4 })

    await waitFor(() => expect(acknowledgePersonalAssistantRead).toHaveBeenCalledTimes(3))
  })

  it('waits to acknowledge until the assistant is visible and scrolled to the bottom', async () => {
    mountBottomedThreadViewport()
    $threadScrolledUp.set(true)
    renderSituation()

    await Promise.resolve()
    expect(acknowledgePersonalAssistantRead).not.toHaveBeenCalled()

    $threadScrolledUp.set(false)
    await waitFor(() => expect(acknowledgePersonalAssistantRead).toHaveBeenCalledTimes(1))
  })

  it('waits for the transcript viewport to load before acknowledging', async () => {
    renderSituation()

    await Promise.resolve()
    expect(acknowledgePersonalAssistantRead).not.toHaveBeenCalled()

    mountBottomedThreadViewport()

    await waitFor(() => expect(acknowledgePersonalAssistantRead).toHaveBeenCalledTimes(1))
  })

  it('coalesces transcript mutations while a read acknowledgement is in flight', async () => {
    let resolveAcknowledgement: (value: undefined) => void = () => undefined

    acknowledgePersonalAssistantRead.mockImplementationOnce(
      () =>
        new Promise<undefined>(resolve => {
          resolveAcknowledgement = resolve
        })
    )
    mountBottomedThreadViewport()
    renderSituation()
    await waitFor(() => expect(acknowledgePersonalAssistantRead).toHaveBeenCalledTimes(1))

    document.body.append(document.createElement('span'), document.createElement('span'))
    await Promise.resolve()

    expect(acknowledgePersonalAssistantRead).toHaveBeenCalledTimes(1)
    resolveAcknowledgement(undefined)
  })

  it('acknowledges unread activity when a bottomed assistant window becomes visible', async () => {
    mountBottomedThreadViewport()
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })
    renderSituation()

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

    renderSituation()
    expandSituation()

    const outcome = screen.getByText('לסיים את תכנון השבוע')
    const pending = screen.getByText('להכין לוח שנה לאירועים')

    expect(screen.getByRole('dialog', { name: 'Assistant context' }).getAttribute('dir')).toBe('ltr')
    expect(outcome.getAttribute('dir')).toBe('auto')
    expect(outcome.className).toContain('text-start')
    expect(pending.closest('li')?.getAttribute('dir')).toBe('auto')

    fireEvent.click(screen.getByRole('button', { name: 'Edit לסיים את תכנון השבוע' }))
    expect(screen.getByRole('textbox', { name: 'Edit לסיים את תכנון השבוע' }).getAttribute('dir')).toBe('auto')
  })

  it('edits an item through a versioned state operation', async () => {
    renderSituation()
    expandSituation()

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
    renderSituation()
    expandSituation()

    fireEvent.click(screen.getByRole('button', { name: 'Archive Waiting for approval' }))

    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('State changed elsewhere'))
  })
})
