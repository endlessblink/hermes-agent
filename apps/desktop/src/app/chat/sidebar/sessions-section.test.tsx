import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SESSION_FOLDER_DROP_EVENT } from '@/app/chat/composer/inline-refs'
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

// Regression net for the pointer-drag → folder drop bridge (2026-07-18): the
// upstream pointer drag replaced native session drags, so these sections'
// native onDrop can never fire for them. The drag session instead targets
// [data-session-folder-drop] and dispatches SESSION_FOLDER_DROP_EVENT on it.
// If either side of that contract regresses, drag-a-chat-into-a-folder dies
// silently — exactly the severed-wire failure the upstream merge caused.
describe('SidebarSessionsSection pointer-drag folder drop', () => {
  afterEach(cleanup)

  const renderSection = (onDropSession?: (sessionId: string) => void) =>
    render(
      <SidebarSessionsSection
        activeSessionId={null}
        emptyState={null}
        label="Folder"
        onArchiveSession={vi.fn()}
        onDeleteSession={vi.fn()}
        onDropSession={onDropSession}
        onResumeSession={vi.fn()}
        onToggle={vi.fn()}
        onTogglePin={vi.fn()}
        open
        pinned={false}
        sessions={[session()]}
        workingSessionIdSet={new Set()}
      />
    )

  it('marks the group as a pointer-drag drop target only when onDropSession is provided', () => {
    const withHandler = renderSection(vi.fn())

    expect(withHandler.container.querySelector('[data-session-folder-drop]')).toBeTruthy()
    cleanup()

    const withoutHandler = renderSection(undefined)

    expect(withoutHandler.container.querySelector('[data-session-folder-drop]')).toBeNull()
  })

  it('runs onDropSession when the pointer drag dispatches the drop event on the group', () => {
    const onDropSession = vi.fn()
    const { container } = renderSection(onDropSession)
    const target = container.querySelector('[data-session-folder-drop]')

    expect(target).toBeTruthy()
    target?.dispatchEvent(new CustomEvent(SESSION_FOLDER_DROP_EVENT, { detail: { sessionId: 'dragged-session' } }))

    expect(onDropSession).toHaveBeenCalledWith('dragged-session')
  })

  it('ignores the drop event when it carries no session id', () => {
    const onDropSession = vi.fn()
    const { container } = renderSection(onDropSession)

    container
      .querySelector('[data-session-folder-drop]')
      ?.dispatchEvent(new CustomEvent(SESSION_FOLDER_DROP_EVENT, { detail: {} }))

    expect(onDropSession).not.toHaveBeenCalled()
  })
})
