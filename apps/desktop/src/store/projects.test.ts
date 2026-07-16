import { beforeEach, describe, expect, it, vi } from 'vitest'

import { $sidebarAgentsGrouped } from '@/store/layout'

import {
  $activeProjectId,
  $newWorktreeRequest,
  $projectScope,
  $projectsRpcAvailable,
  $projectTree,
  $startWorkSessionRequest,
  $worktreeRefreshToken,
  ALL_PROJECTS,
  createProject,
  enterProject,
  exitProjectScope,
  followActiveSessionCwd,
  openProjectCreate,
  pickProjectFolder,
  refreshProjects,
  refreshWorktrees,
  requestNewWorktree,
  requestStartWorkSession,
  startWorkInRepo
} from './projects'

vi.mock('@/i18n', () => ({
  translateNow: (key: string) => key
}))

vi.mock('@/store/notifications', () => ({
  notify: vi.fn()
}))

vi.mock('@/lib/desktop-git', () => ({
  desktopGit: vi.fn()
}))

vi.mock('@/lib/desktop-fs', () => ({
  desktopDefaultCwd: vi.fn(),
  isDesktopFsRemoteMode: vi.fn(),
  selectDesktopPaths: vi.fn(),
  writeDesktopFileText: vi.fn()
}))

vi.mock('@/store/gateway', () => ({
  activeGateway: vi.fn(),
  ensureActiveGatewayOpen: vi.fn()
}))

const fs = await import('@/lib/desktop-fs')
const desktopDefaultCwd = vi.mocked(fs.desktopDefaultCwd)
const isDesktopFsRemoteMode = vi.mocked(fs.isDesktopFsRemoteMode)
const selectDesktopPaths = vi.mocked(fs.selectDesktopPaths)

const gw = await import('@/store/gateway')
const activeGateway = vi.mocked(gw.activeGateway)
const notifications = await import('@/store/notifications')
const notify = vi.mocked(notifications.notify)
const git = await import('@/lib/desktop-git')
const desktopGit = vi.mocked(git.desktopGit)

describe('project scope', () => {
  beforeEach(() => {
    window.localStorage.clear()
    $projectScope.set(ALL_PROJECTS)
  })

  it('defaults to ALL_PROJECTS', () => {
    expect($projectScope.get()).toBe(ALL_PROJECTS)
  })

  it('enterProject scopes the sidebar to the project id', () => {
    // setActiveProject fires best-effort (no gateway in test → it rejects and is
    // swallowed); the synchronous scope change is what matters here.
    enterProject('p_123')
    expect($projectScope.get()).toBe('p_123')
  })

  it('exitProjectScope returns to the overview', () => {
    enterProject('p_123')
    exitProjectScope()
    expect($projectScope.get()).toBe(ALL_PROJECTS)
  })

  it('entering the synthetic No-project bucket still scopes (no active pin)', () => {
    enterProject('__no_project__')
    expect($projectScope.get()).toBe('__no_project__')
  })

  it('persists the scope to localStorage', () => {
    enterProject('p_abc')
    expect(window.localStorage.getItem('hermes.desktop.projectScope')).toBe('p_abc')
  })
})

describe('worktree refresh', () => {
  it('refreshWorktrees bumps the probe token so useRepoWorktreeMap refetches', () => {
    const before = $worktreeRefreshToken.get()
    refreshWorktrees()
    expect($worktreeRefreshToken.get()).toBe(before + 1)
  })

  it('ignores worktree creation requests while the folder view is selected', () => {
    $sidebarAgentsGrouped.set(false)
    const before = $newWorktreeRequest.get()

    requestNewWorktree()

    expect($newWorktreeRequest.get()).toBe(before)
  })

  it('ignores worktree session requests while the folder view is selected', () => {
    $sidebarAgentsGrouped.set(false)
    $startWorkSessionRequest.set(null)

    requestStartWorkSession('/repo/.worktrees/feature')

    expect($startWorkSessionRequest.get()).toBeNull()
  })

  it('does not create a worktree while the folder view is selected', async () => {
    $sidebarAgentsGrouped.set(false)
    const worktreeAdd = vi.fn(async () => ({ branch: 'feature', path: '/repo/.worktrees/feature' }))
    desktopGit.mockReturnValue({ worktreeAdd } as never)

    await expect(startWorkInRepo('/repo', { branch: 'feature' })).resolves.toBeNull()
    expect(worktreeAdd).not.toHaveBeenCalled()
  })

  it('keeps worktree requests available after the user explicitly selects Projects', async () => {
    $sidebarAgentsGrouped.set(true)
    $startWorkSessionRequest.set(null)
    const before = $newWorktreeRequest.get()
    const worktreeAdd = vi.fn(async () => ({ branch: 'feature', path: '/repo/.worktrees/feature' }))
    desktopGit.mockReturnValue({ worktreeAdd } as never)

    requestNewWorktree()
    requestStartWorkSession('/repo/.worktrees/feature')

    await expect(startWorkInRepo('/repo', { branch: 'feature' })).resolves.toEqual({
      branch: 'feature',
      path: '/repo/.worktrees/feature'
    })
    expect($newWorktreeRequest.get()).toBe(before + 1)
    expect($startWorkSessionRequest.get()?.path).toBe('/repo/.worktrees/feature')
    expect(worktreeAdd).toHaveBeenCalledOnce()
  })
})

describe('following workspace changes', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    $projectScope.set(ALL_PROJECTS)
    $projectTree.set([])
  })

  it('keeps the folder view selected when a chat moves into a worktree', async () => {
    $sidebarAgentsGrouped.set(false)

    const project = {
      id: 'p_demo',
      label: 'Demo',
      path: '/repo',
      repos: [
        {
          groups: [
            {
              id: '/repo/.worktrees/feature',
              label: 'feature',
              path: '/repo/.worktrees/feature',
              sessions: []
            }
          ],
          id: '/repo',
          label: 'repo',
          path: '/repo',
          sessionCount: 0
        }
      ],
      sessionCount: 0
    }

    const request = vi.fn(async (method: string) =>
      method === 'projects.tree'
        ? { active_id: null, projects: [project], scoped_session_ids: [] }
        : { active_id: null, projects: [] }
    )

    activeGateway.mockReturnValue({ connectionState: 'open', request } as never)

    await followActiveSessionCwd('/repo/.worktrees/feature')

    expect($sidebarAgentsGrouped.get()).toBe(false)
    expect($projectScope.get()).toBe(ALL_PROJECTS)
  })

  it('follows the chat inside Projects after the user explicitly selects that view', async () => {
    $sidebarAgentsGrouped.set(true)

    const project = {
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
    }

    const request = vi.fn(async (method: string) =>
      method === 'projects.tree'
        ? { active_id: null, projects: [project], scoped_session_ids: [] }
        : { active_id: null, projects: [] }
    )

    activeGateway.mockReturnValue({ connectionState: 'open', request } as never)

    await followActiveSessionCwd('/repo')

    expect($projectScope.get()).toBe('p_demo')
  })
})

describe('pickProjectFolder', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('uses the remote-aware directory picker locally', async () => {
    isDesktopFsRemoteMode.mockReturnValue(false)
    selectDesktopPaths.mockResolvedValue(['/local/repo'])

    await expect(pickProjectFolder()).resolves.toBe('/local/repo')
    expect(selectDesktopPaths).toHaveBeenCalledWith({ defaultPath: undefined, directories: true, multiple: false })
  })

  it('seeds the picker with the backend cwd on a remote gateway', async () => {
    isDesktopFsRemoteMode.mockReturnValue(true)
    desktopDefaultCwd.mockResolvedValue({ branch: 'main', cwd: '/backend/work' })
    selectDesktopPaths.mockResolvedValue(['/backend/work/repo'])

    await expect(pickProjectFolder()).resolves.toBe('/backend/work/repo')
    expect(selectDesktopPaths).toHaveBeenCalledWith({
      defaultPath: '/backend/work',
      directories: true,
      multiple: false
    })
  })

  it('returns null when the picker is cancelled (empty selection)', async () => {
    isDesktopFsRemoteMode.mockReturnValue(false)
    selectDesktopPaths.mockResolvedValue([])

    await expect(pickProjectFolder()).resolves.toBeNull()
  })
})

describe('createProject', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    $sidebarAgentsGrouped.set(false)
    $activeProjectId.set(null)
    $projectsRpcAvailable.set(null)
  })

  it('creates the project without replacing the selected folder view', async () => {
    const created = { folders: [], id: 'p_new', name: 'Demo', primary_path: '/srv/demo' }

    const request = vi.fn(async (method: string) => {
      if (method === 'projects.create') {
        return { project: created }
      }

      // Reconcile (fire-and-forget) re-reads list + tree; echo the project back
      // so the optimistic state survives instead of being wiped to empty.
      return { active_id: 'p_new', projects: [created], scoped_session_ids: [] }
    })

    activeGateway.mockReturnValue({ connectionState: 'open', request } as never)

    const result = await createProject({ folders: ['/srv/demo'], name: 'Demo', use: true })

    expect(result).toEqual(created)
    expect(request).toHaveBeenCalledWith('projects.create', expect.objectContaining({ name: 'Demo' }))
    expect($sidebarAgentsGrouped.get()).toBe(false)
    expect($activeProjectId.get()).toBe('p_new')
  })

  it('marks the backend stale and surfaces a friendly error when projects.create is missing', async () => {
    activeGateway.mockReturnValue({
      connectionState: 'open',
      request: vi.fn().mockRejectedValue(new Error('unknown method: projects.create'))
    } as never)

    await expect(createProject({ folders: ['/srv/demo'], name: 'Demo' })).rejects.toThrow(
      'sidebar.projects.staleBackend'
    )
    expect($projectsRpcAvailable.get()).toBe(false)
  })
})

describe('projects RPC capability', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    $projectsRpcAvailable.set(null)
  })

  it('marks the backend stale when projects.list is missing', async () => {
    activeGateway.mockReturnValue({
      connectionState: 'open',
      request: vi.fn().mockRejectedValue(new Error('unknown method: projects.list'))
    } as never)

    await refreshProjects()

    expect($projectsRpcAvailable.get()).toBe(false)
  })

  it('blocks opening the create dialog once the backend is known stale', () => {
    $projectsRpcAvailable.set(false)

    openProjectCreate()

    expect(notify).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'warning', message: 'sidebar.projects.staleBackend' })
    )
  })
})
