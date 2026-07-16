import { beforeEach, describe, expect, it, vi } from 'vitest'

const STORAGE_KEY = 'hermes.desktop.workspacePresentation'

async function loadStore() {
  vi.resetModules()

  return import('./workspace-presentation')
}

describe('workspace presentation', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('defaults safely to folders without changing Git state', async () => {
    const { $workspacePresentation } = await loadStore()

    expect($workspacePresentation.get()).toBe('folders')
  })

  it('persists an explicit worktree view and restores it', async () => {
    const { $workspacePresentation } = await loadStore()

    $workspacePresentation.set('worktrees')
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe('worktrees')

    const restored = await loadStore()
    expect(restored.$workspacePresentation.get()).toBe('worktrees')
  })

  it('falls back to folders for an invalid stored value', async () => {
    window.localStorage.setItem(STORAGE_KEY, 'surprise')

    const { $workspacePresentation } = await loadStore()
    expect($workspacePresentation.get()).toBe('folders')
  })
})
