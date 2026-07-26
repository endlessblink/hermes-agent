import { beforeEach, describe, expect, it, vi } from 'vitest'

import { quitHermes } from './quit-app'

describe('quitHermes', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
  })

  it('requests a full desktop shutdown through the preload bridge', async () => {
    const quit = vi.fn(async () => ({ quitting: true }))
    vi.stubGlobal('window', { hermesDesktop: { quit } })

    await expect(quitHermes()).resolves.toEqual({ quitting: true })
    expect(quit).toHaveBeenCalledOnce()
  })
})
