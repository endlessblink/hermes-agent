import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SessionInfo } from '@/types/hermes'

import { SidebarSessionsSection } from './sessions-section'

const session = (overrides: Partial<SessionInfo> = {}): SessionInfo => ({
  ended_at: null,
  id: 'selected-post-chat',
  input_tokens: 0,
  is_active: false,
  last_active: 10,
  message_count: 3,
  model: null,
  output_tokens: 0,
  preview: null,
  source: null,
  started_at: 1,
  title: 'Post about my new website',
  tool_call_count: 0,
  ...overrides
})

describe('SidebarSessionsSection project overview', () => {
  afterEach(cleanup)

  it('renders an explicitly supplied current loose session alongside project rows', () => {
    render(
      <SidebarSessionsSection
        activeSessionId="selected-post-chat"
        emptyState={null}
        label="Projects"
        onArchiveSession={vi.fn()}
        onDeleteSession={vi.fn()}
        onResumeSession={vi.fn()}
        onToggle={vi.fn()}
        onTogglePin={vi.fn()}
        open
        pinned={false}
        projectOverview={[
          {
            id: '/www/app',
            isAuto: true,
            label: 'app',
            path: '/www/app',
            repos: [],
            sessionCount: 0
          }
        ]}
        sessions={[session({ cwd: null })]}
        workingSessionIdSet={new Set()}
      />
    )

    expect(screen.getByText('Post about my new website')).toBeTruthy()
    expect(screen.getByText('app')).toBeTruthy()
  })
})
