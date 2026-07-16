import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type * as React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { gatewayForProfile, request } = vi.hoisted(() => ({
  gatewayForProfile: vi.fn(),
  request: vi.fn()
}))

vi.mock('@/store/gateway', () => ({ gatewayForProfile }))
vi.mock('@/store/notifications', () => ({ notifyError: vi.fn() }))
vi.mock('@/components/ui/codicon', () => ({ Codicon: () => <span aria-hidden="true" /> }))
vi.mock('@/components/ui/sidebar', () => ({
  SidebarMenuButton: ({ children, tooltip, ...props }: React.ComponentProps<'button'> & { tooltip?: string }) => (
    <button aria-label={tooltip} {...props}>
      {children}
    </button>
  ),
  SidebarMenuItem: ({ children }: React.ComponentProps<'li'>) => <li>{children}</li>
}))

import { $personalAssistantState, type AssistantState } from '@/store/personal-assistant'

import { PersonalAssistantSidebarRow } from './personal-assistant-row'

function assistantState(version: number, unreadCount: number): AssistantState {
  return {
    blockers: [],
    capacity: { summary: null, updatedAt: null },
    captureProposals: [],
    commitments: [],
    deferred: [],
    episodes: [],
    focus: null,
    outcomes: [],
    pendingApprovals: [],
    schemaVersion: 1,
    sessionId: 'assistant-home',
    sync: { lastCheckedAt: null, lastVerifiedAt: null, status: 'unknown' },
    unreadCount,
    version
  }
}

function row(onOpenSession = vi.fn()) {
  return render(
    <PersonalAssistantSidebarRow
      contentVisible
      currentView="chat"
      gatewayState="open"
      onOpenSession={onOpenSession}
      selectedSessionId={null}
      sessions={[]}
    />
  )
}

describe('PersonalAssistantSidebarRow', () => {
  beforeEach(() => {
    request.mockReset()
    gatewayForProfile.mockReset()
    gatewayForProfile.mockResolvedValue({ request })
    $personalAssistantState.set(null)
  })

  afterEach(() => cleanup())

  it('hydrates the owner-profile unread badge when the gateway is ready', async () => {
    request.mockResolvedValue({ state: assistantState(4, 3) })

    row()

    expect((await screen.findByLabelText('3 unread assistant updates')).textContent).toContain('3')
    expect(gatewayForProfile).toHaveBeenCalledWith('office-work')
    expect(request).toHaveBeenCalledWith('personal_assistant.state.get', { profile: 'office-work' })
  })

  it('opens the canonical home and clears the badge from the acknowledged snapshot', async () => {
    const onOpenSession = vi.fn()

    $personalAssistantState.set(assistantState(7, 2))
    request.mockResolvedValue({
      canonical_session_id: 'assistant-home',
      session_id: 'assistant-live',
      state: assistantState(8, 0),
      status: 'ready'
    })

    row(onOpenSession)
    fireEvent.click(screen.getByRole('button', { name: 'Personal assistant' }))

    await waitFor(() => expect(onOpenSession).toHaveBeenCalledWith('assistant-home'))
    expect(request).toHaveBeenCalledWith('personal_assistant.home', { profile: 'office-work' })
    expect(screen.queryByLabelText(/unread assistant update/)).toBeNull()
  })

  it('retries hydration and refreshes later unread attention while mounted', async () => {
    let poll: (() => void) | undefined

    const interval = vi.spyOn(window, 'setInterval').mockImplementation(callback => {
      poll = callback as () => void

      return 41 as never
    })

    request.mockRejectedValueOnce(new Error('owner gateway starting'))

    try {
      row()
      await act(async () => undefined)
      expect(request).toHaveBeenCalledTimes(1)
      expect(poll).toBeTypeOf('function')

      request.mockResolvedValueOnce({ state: assistantState(4, 2) })
      await act(async () => poll?.())
      expect(screen.getByLabelText('2 unread assistant updates').textContent).toContain('2')

      request.mockResolvedValueOnce({ state: assistantState(5, 4) })
      await act(async () => poll?.())
      expect(screen.getByLabelText('4 unread assistant updates').textContent).toContain('4')
    } finally {
      interval.mockRestore()
    }
  })
})
