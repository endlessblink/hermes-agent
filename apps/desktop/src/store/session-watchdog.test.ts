import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  $workingSessionIds,
  clearWorkingSessionsForProfile,
  onSessionWatchdogClear,
  setSessionWorking,
  setWorkingSessionIds
} from './session'

const WATCHDOG_MS = 75 * 1000

describe('session watchdog', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    setWorkingSessionIds(() => [])
  })

  afterEach(() => {
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
  })

  it('drops a stuck session and notifies listeners once the silence window elapses', () => {
    const cleared: string[] = []
    const off = onSessionWatchdogClear(id => cleared.push(id))

    setSessionWorking('s1', true)
    expect($workingSessionIds.get()).toContain('s1')

    vi.advanceTimersByTime(WATCHDOG_MS - 1)
    expect($workingSessionIds.get()).toContain('s1')
    expect(cleared).toEqual([])

    vi.advanceTimersByTime(1)

    // Both the sidebar dot AND the busy-clearing signal fire — the contract
    // that lets the composer recover from a hung/looping turn, not just the dot.
    expect($workingSessionIds.get()).not.toContain('s1')
    expect(cleared).toEqual(['s1'])

    off()
  })

  it('never fires for a session that settles before the window', () => {
    const cleared: string[] = []
    const off = onSessionWatchdogClear(id => cleared.push(id))

    setSessionWorking('s2', true)
    setSessionWorking('s2', false)

    vi.advanceTimersByTime(WATCHDOG_MS)

    expect(cleared).toEqual([])

    off()
  })

  it('stops notifying after unsubscribe', () => {
    const cleared: string[] = []
    const off = onSessionWatchdogClear(id => cleared.push(id))
    off()

    setSessionWorking('s3', true)
    vi.advanceTimersByTime(WATCHDOG_MS)

    expect(cleared).toEqual([])
  })

  it('releases only the sessions owned by a backend that exits', () => {
    const cleared: Array<[string, string]> = []
    const off = onSessionWatchdogClear((id, reason) => cleared.push([id, reason]))

    setSessionWorking('bina-running', true, 'bina-meatzevet')
    setSessionWorking('office-running', true, 'office-work')

    clearWorkingSessionsForProfile('bina-meatzevet')

    expect($workingSessionIds.get()).toEqual(['office-running'])
    expect(cleared).toEqual([['bina-running', 'backend_exit']])

    off()
  })
})
