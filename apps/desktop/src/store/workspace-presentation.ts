import { persistentAtom } from '@/lib/persisted'

export type WorkspacePresentation = 'folders' | 'worktrees'

const STORAGE_KEY = 'hermes.desktop.workspacePresentation'

export const $workspacePresentation = persistentAtom<WorkspacePresentation>(STORAGE_KEY, 'folders', {
  decode: raw => (raw === 'worktrees' ? 'worktrees' : 'folders'),
  encode: value => value
})

export function showWorktreeControls(): boolean {
  return $workspacePresentation.get() === 'worktrees'
}
