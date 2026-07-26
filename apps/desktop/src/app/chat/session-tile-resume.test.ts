import { describe, expect, it } from 'vitest'

import { canAttemptSessionTileResume } from './session-tile'

describe('canAttemptSessionTileResume', () => {
  it('wakes a profile-owned Personal Assistant tile independently of the foreground gateway', () => {
    expect(canAttemptSessionTileResume('office-work', false)).toBe(true)
  })

  it('keeps an ordinary tile gated on its foreground gateway', () => {
    expect(canAttemptSessionTileResume(undefined, false)).toBe(false)
    expect(canAttemptSessionTileResume(undefined, true)).toBe(true)
  })
})
