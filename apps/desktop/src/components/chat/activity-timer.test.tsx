import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { __resetElapsedTimerRegistryForTests, useElapsedSeconds } from './activity-timer'

function Probe({ active, startedAtMs, timerKey }: { active: boolean; startedAtMs?: number; timerKey?: string }) {
  const elapsed = useElapsedSeconds(active, timerKey, startedAtMs)

  return <span data-testid="elapsed">{elapsed}</span>
}

describe('useElapsedSeconds', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-01-01T00:00:00.000Z'))
    __resetElapsedTimerRegistryForTests()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    __resetElapsedTimerRegistryForTests()
  })

  it('keeps elapsed time stable across remounts for the same key', () => {
    const first = render(<Probe active timerKey="tool:abc" />)

    act(() => {
      vi.advanceTimersByTime(5_000)
    })

    expect(screen.getByTestId('elapsed').textContent).toBe('5')

    first.unmount()

    act(() => {
      vi.advanceTimersByTime(3_000)
    })

    render(<Probe active timerKey="tool:abc" />)

    expect(screen.getByTestId('elapsed').textContent).toBe('8')
  })

  it('uses a stable turn start when a remounted message has a different id', () => {
    const turnStartedAt = Date.now()
    const first = render(<Probe active startedAtMs={turnStartedAt} timerKey="reasoning:temporary-message-a" />)

    act(() => {
      vi.advanceTimersByTime(5_000)
    })

    expect(first.getByTestId('elapsed').textContent).toBe('5')

    first.unmount()

    act(() => {
      vi.advanceTimersByTime(3_000)
    })

    const second = render(<Probe active startedAtMs={turnStartedAt} timerKey="reasoning:temporary-message-b" />)

    expect(second.getByTestId('elapsed').textContent).toBe('8')
  })
})
