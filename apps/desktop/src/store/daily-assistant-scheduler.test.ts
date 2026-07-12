import { atom } from 'nanostores'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { nextJerusalemNine, startDailyAssistantScheduler } from './daily-assistant-scheduler'

afterEach(() => {
  vi.useRealTimers()
})

describe('daily assistant scheduler', () => {
  it('fires at 09:00 Jerusalem while office-work is selected', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-12T05:59:00.000Z')) // 08:59 Jerusalem
    const profile = atom('office-work')
    const launch = vi.fn(async () => undefined)
    const stop = startDailyAssistantScheduler(profile, launch)

    expect(launch).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(60_000)

    expect(launch).toHaveBeenCalledWith('office-work', 'scheduled')
    await vi.advanceTimersByTimeAsync(24 * 60 * 60 * 1000)
    expect(launch).toHaveBeenCalledTimes(2)
    stop()
  })

  it('invokes immediately after nine and ignores other profiles', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-12T10:00:00.000Z'))
    const profile = atom('default')
    const launch = vi.fn(async () => undefined)
    const stop = startDailyAssistantScheduler(profile, launch)

    profile.set('office-work')
    await Promise.resolve()

    expect(launch).toHaveBeenCalledWith('office-work', 'scheduled')
    stop()
  })

  it('computes Jerusalem nine across standard and daylight time', () => {
    expect(nextJerusalemNine(new Date('2026-01-12T05:00:00.000Z')).toISOString()).toBe(
      '2026-01-12T07:00:00.000Z'
    )
    expect(nextJerusalemNine(new Date('2026-07-12T05:00:00.000Z')).toISOString()).toBe(
      '2026-07-12T06:00:00.000Z'
    )
  })
})
