import { describe, expect, it } from 'vitest'

import { shouldResetTileRuntimeBinding } from './session-states'

describe('shouldResetTileRuntimeBinding', () => {
  it('preserves a Personal Assistant runtime when an unrelated foreground profile reconnects', () => {
    expect(shouldResetTileRuntimeBinding('office-work', 'flowstate-reliability-worktree')).toBe(false)
  })

  it('resets tiles owned by the reconnected gateway and legacy foreground tiles', () => {
    expect(shouldResetTileRuntimeBinding('office-work', 'office-work')).toBe(true)
    expect(shouldResetTileRuntimeBinding(undefined, 'office-work')).toBe(true)
  })
})
