import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { HermesRepoStatus } from '@/global'

import { $repoStatus, $repoWorktrees, refreshRepoStatus } from './coding-status'
import { $sidebarAgentsGrouped } from './layout'
import { $currentCwd } from './session'

const sampleStatus: HermesRepoStatus = {
  branch: 'feature/login',
  defaultBranch: 'main',
  detached: false,
  ahead: 1,
  behind: 0,
  staged: 1,
  unstaged: 2,
  untracked: 0,
  conflicted: 0,
  changed: 3,
  added: 12,
  removed: 4,
  files: []
}

function stubProbe(impl: (cwd: string) => Promise<HermesRepoStatus | null>) {
  ;(window as unknown as { hermesDesktop?: unknown }).hermesDesktop = { git: { repoStatus: impl } }
}

describe('refreshRepoStatus', () => {
  beforeEach(() => {
    $sidebarAgentsGrouped.set(false)
    $repoStatus.set(null)
    $repoWorktrees.set([])
    $currentCwd.set('')
    delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
  })

  afterEach(() => {
    delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
  })

  it('populates $repoStatus from the probe for an explicit cwd', async () => {
    stubProbe(async () => sampleStatus)
    await refreshRepoStatus('/repo')
    expect($repoStatus.get()).toEqual(sampleStatus)
  })

  it('does not scan for worktrees while the folder view is selected', async () => {
    const worktreeList = vi.fn(async () => [
      { branch: 'feature', detached: false, isMain: false, locked: false, path: '/repo/.worktrees/feature' }
    ])

    ;(window as unknown as { hermesDesktop?: unknown }).hermesDesktop = {
      git: { repoStatus: async () => sampleStatus, worktreeList }
    }

    await refreshRepoStatus('/repo')

    expect(worktreeList).not.toHaveBeenCalled()
    expect($repoWorktrees.get()).toEqual([])
  })

  it('scans for worktrees after the user explicitly selects Projects', async () => {
    $sidebarAgentsGrouped.set(true)

    const worktrees = [
      { branch: 'feature', detached: false, isMain: false, locked: false, path: '/repo/.worktrees/feature' }
    ]

    const worktreeList = vi.fn(async () => worktrees)

    ;(window as unknown as { hermesDesktop?: unknown }).hermesDesktop = {
      git: { repoStatus: async () => sampleStatus, worktreeList }
    }

    await refreshRepoStatus('/repo')
    await vi.waitFor(() => expect($repoWorktrees.get()).toEqual(worktrees))

    expect(worktreeList).toHaveBeenCalledWith('/repo')
  })

  it('falls back to the active session cwd when none is passed', async () => {
    const probe = vi.fn(async () => sampleStatus)
    stubProbe(probe)
    $currentCwd.set('/active/repo')
    await refreshRepoStatus()
    expect(probe).toHaveBeenCalledWith('/active/repo')
  })

  it('clears status when there is no cwd', async () => {
    stubProbe(async () => sampleStatus)
    $repoStatus.set(sampleStatus)
    await refreshRepoStatus('   ')
    expect($repoStatus.get()).toBeNull()
  })

  it('clears status when the probe is unavailable (remote backend)', async () => {
    $repoStatus.set(sampleStatus)
    await refreshRepoStatus('/repo')
    expect($repoStatus.get()).toBeNull()
  })

  it('clears status when the probe throws', async () => {
    stubProbe(async () => {
      throw new Error('not a repo')
    })
    $repoStatus.set(sampleStatus)
    await refreshRepoStatus('/repo')
    expect($repoStatus.get()).toBeNull()
  })
})
