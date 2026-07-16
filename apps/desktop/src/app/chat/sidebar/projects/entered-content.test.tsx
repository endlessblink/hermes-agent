import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { $dismissedWorktreeIds } from '@/store/layout'

import { EnteredProjectContent } from './entered-content'

describe('EnteredProjectContent hidden worktrees', () => {
  afterEach(() => {
    cleanup()
    $dismissedWorktreeIds.set([])
  })

  it('keeps an on-disk worktree hidden after the user removes it from the sidebar', () => {
    const worktreePath = '/repo/.worktrees/feature'
    $dismissedWorktreeIds.set([worktreePath])

    const view = render(
      <EnteredProjectContent
        project={{
          id: 'p_demo',
          label: 'Demo',
          path: '/repo',
          repos: [
            {
              groups: [],
              id: '/repo',
              label: 'repo',
              path: '/repo',
              sessionCount: 0
            }
          ],
          sessionCount: 0
        }}
        renderRows={() => null}
        repoWorktrees={{
          '/repo': [{ branch: 'feature', detached: false, isMain: false, locked: false, path: worktreePath }]
        }}
      />
    )

    expect(view.container.textContent).not.toContain('feature')
  })
})
