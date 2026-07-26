import { describe, expect, it } from 'vitest'

import { profileSwapTargetForSurface } from './chat-swap-overlay'

describe('profileSwapTargetForSurface', () => {
  it('never masks a pinned session tile during an unrelated profile swap', () => {
    expect(profileSwapTargetForSurface('office-work', 'tile')).toBeNull()
  })

  it('keeps the profile transition visible on the primary chat', () => {
    expect(profileSwapTargetForSurface('office-work', 'primary')).toBe('office-work')
  })
})
