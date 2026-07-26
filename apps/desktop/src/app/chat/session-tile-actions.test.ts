import { describe, expect, it, vi } from 'vitest'

import { recoverSessionTileRuntime } from './session-tile-actions'

describe('recoverSessionTileRuntime', () => {
  it('rebinds the same stored tile to the runtime created after a backend restart', async () => {
    const runtimeIdRef = { current: 'dead-runtime' }
    const resumeTile = vi.fn(async () => 'replacement-runtime')
    const patchTile = vi.fn()

    await recoverSessionTileRuntime({
      patchTile,
      profile: 'office-work',
      resumeTile,
      runtimeIdRef,
      storedSessionId: '20260724_015217_72d998'
    })

    expect(resumeTile).toHaveBeenCalledWith('20260724_015217_72d998', 'office-work')
    expect(runtimeIdRef.current).toBe('replacement-runtime')
    expect(patchTile).toHaveBeenCalledWith('20260724_015217_72d998', {
      error: undefined,
      runtimeId: 'replacement-runtime'
    })
  })
})
