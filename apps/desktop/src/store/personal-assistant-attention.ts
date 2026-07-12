import { dispatchNativeNotification } from '@/store/native-notifications'
import { notify } from '@/store/notifications'
import {
  $personalAssistantPendingCount,
  $personalAssistantState,
  refreshPersonalAssistantState
} from '@/store/personal-assistant'

export interface PersonalAssistantAttentionPayload {
  episode_id: string
  kind: string
  message?: string
  pending_count: number
  session_id: string
  title?: string
  unread_count: number
}

const seenEpisodes = new Set<string>()
const MAX_SEEN_EPISODES = 200

export async function handlePersonalAssistantAttention(
  payload: PersonalAssistantAttentionPayload,
  openHome: () => void
): Promise<boolean> {
  if (!payload.episode_id || seenEpisodes.has(payload.episode_id)) {
    return false
  }

  seenEpisodes.add(payload.episode_id)
  $personalAssistantPendingCount.set(payload.pending_count)

  if (seenEpisodes.size > MAX_SEEN_EPISODES) {
    seenEpisodes.delete(seenEpisodes.values().next().value as string)
  }

  try {
    await refreshPersonalAssistantState()
  } catch {
    const current = $personalAssistantState.get()

    if (current) {
      $personalAssistantState.set({ ...current, unreadCount: payload.unread_count })
    }
  }

  const title = payload.title || 'Personal assistant needs your attention'
  const message = payload.message || `${payload.pending_count} items are waiting for you.`

  notify({
    action: { label: 'View', onClick: openHome },
    icon: 'sparkle',
    id: `personal-assistant:${payload.episode_id}`,
    kind: 'info',
    message,
    title
  })
  dispatchNativeNotification({
    actions: [{ id: 'view-personal-assistant', text: 'View' }],
    body: message,
    kind: 'input',
    sessionId: payload.session_id,
    title
  })

  return true
}
