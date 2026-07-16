import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { $repoStatus, $repoWorktrees } from '@/store/coding-status'
import { $sidebarAgentsGrouped } from '@/store/layout'

import { CodingStatusRow } from './coding-row'

describe('CodingStatusRow folder mode', () => {
  beforeEach(() => {
    $sidebarAgentsGrouped.set(false)
    $repoStatus.set({
      added: 0,
      ahead: 0,
      behind: 0,
      branch: 'main',
      changed: 0,
      conflicted: 0,
      defaultBranch: 'main',
      detached: false,
      files: [],
      removed: 0,
      staged: 0,
      unstaged: 0,
      untracked: 0
    })
    $repoWorktrees.set([
      { branch: 'feature', detached: false, isMain: false, locked: false, path: '/repo/.worktrees/feature' }
    ])
  })

  afterEach(() => {
    cleanup()
    $repoStatus.set(null)
    $repoWorktrees.set([])
  })

  it('keeps git status visible without exposing worktree actions', () => {
    render(
      <CodingStatusRow
        onBranchOff={async () => undefined}
        onConvertBranch={async () => undefined}
        onListBranches={async () => []}
        onOpen={() => undefined}
        onOpenWorktree={() => undefined}
        onSwitchBranch={async () => undefined}
      />
    )

    expect(screen.getByText('main')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /new branch/i })).toBeNull()
  })
})
