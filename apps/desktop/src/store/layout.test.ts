import { afterEach, describe, expect, it, vi } from 'vitest'

describe('sidebar organization preference', () => {
  afterEach(() => {
    window.localStorage.clear()
    vi.resetModules()
  })

  it('starts in folder mode instead of restoring the legacy auto-forced Projects view', async () => {
    window.localStorage.setItem('hermes.desktop.agentsGroupedByWorkspace', 'true')
    vi.resetModules()

    const { $sidebarAgentsGrouped } = await import('./layout')

    expect($sidebarAgentsGrouped.get()).toBe(false)
  })

  it('persists Projects mode only after the user explicitly selects it again', async () => {
    window.localStorage.setItem('hermes.desktop.projectsViewEnabled', 'true')
    vi.resetModules()

    const { $sidebarAgentsGrouped } = await import('./layout')

    expect($sidebarAgentsGrouped.get()).toBe(true)
  })
})
