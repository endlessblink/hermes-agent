import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const fetchPersonalAssistantDayPlan = vi.fn()

vi.mock('@/store/personal-assistant', () => ({ fetchPersonalAssistantDayPlan }))

const { PersonalAssistantToday } = await import('./personal-assistant-today')

function Harness() {
  const [open, setOpen] = useState(true)

  return <PersonalAssistantToday onOpenChange={setOpen} open={open} />
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(2026, 6, 20, 12, 0))
  fetchPersonalAssistantDayPlan.mockReset()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('PersonalAssistantToday', () => {
  it('renders a proportional read-only timeline around today’s real blocks', async () => {
    fetchPersonalAssistantDayPlan.mockResolvedValue({
      blocks: [
        { id: 'block-1', taskId: 'task-1', title: 'Morning review', startTime: '09:15', durationMinutes: 60 },
        { id: 'block-2', taskId: 'task-2', title: 'Launch prep', startTime: '14:30', durationMinutes: 45 }
      ],
      capturedAt: '2026-07-20T08:00:00Z',
      complete: true,
      date: '2026-07-20',
      fresh: true,
      source: 'flowstate'
    })

    render(<Harness />)

    expect(await screen.findByRole('dialog', { name: 'Today' })).toBeTruthy()
    expect(await screen.findByText('Morning review')).toBeTruthy()
    expect(screen.getByText('Launch prep')).toBeTruthy()
    expect(screen.getByLabelText('Current time 12:00')).toBeTruthy()
    expect(screen.getByText('09:15–10:15')).toBeTruthy()
    expect(screen.getByText('14:30–15:15')).toBeTruthy()
    expect(fetchPersonalAssistantDayPlan).toHaveBeenCalledWith('2026-07-20')
  })

  it('shows an honest empty day instead of inventing tasks', async () => {
    fetchPersonalAssistantDayPlan.mockResolvedValue({
      blocks: [],
      capturedAt: '2026-07-20T08:00:00Z',
      complete: true,
      date: '2026-07-20',
      fresh: true,
      source: 'flowstate'
    })

    render(<Harness />)

    expect(await screen.findByText('No timed work blocks today')).toBeTruthy()
  })

  it('keeps a failed FlowState read visible and retryable', async () => {
    fetchPersonalAssistantDayPlan
      .mockRejectedValueOnce(new Error('FlowState day plan is unavailable'))
      .mockResolvedValueOnce({
        blocks: [],
        capturedAt: null,
        complete: true,
        date: '2026-07-20',
        fresh: true,
        source: 'flowstate'
      })

    render(<Harness />)

    expect((await screen.findByRole('alert')).textContent).toContain('FlowState day plan is unavailable')
    fireEvent.click(screen.getByRole('button', { name: 'Retry Today plan' }))

    await waitFor(() => expect(fetchPersonalAssistantDayPlan).toHaveBeenCalledTimes(2))
  })
})
