import { sessionRoute } from '@/app/routes'
import { PERSONAL_ASSISTANT_OWNER_PROFILE, type PersonalAssistantDestination } from '@/store/personal-assistant'

export interface PersonalAssistantDestinationDependencies {
  knownSessionId?: null | string
  loadKnownSessionId?: () => Promise<null | string>
  navigate: (route: string) => void
  openHome: () => Promise<PersonalAssistantDestination>
  resumeSession: (storedSessionId: string) => Promise<void>
}

interface PersonalAssistantEntryDependencies {
  openChat: () => void
  openContext: () => void
}

interface PersonalAssistantContextDependencies {
  onError: (error: unknown) => void
  refresh: () => Promise<unknown>
  setOpen: (open: boolean) => void
}

interface PersonalAssistantLaunchDependencies {
  navigate: (route: string) => void
  refreshState: () => Promise<unknown>
  start: (trigger: 'manual' | 'scheduled') => Promise<{ sessionId?: null | string }>
}

interface PersonalAssistantTabDependencies {
  bindSessionRuntime: (storedSessionId: string, runtimeSessionId: string, profile: string) => void
  focusOpenSession: (storedSessionId: string) => boolean
  openHome: () => Promise<PersonalAssistantDestination>
  openSessionTile: (
    storedSessionId: string,
    dir: 'center',
    anchor?: string,
    before?: null | string,
    profile?: string,
    runtimeId?: string
  ) => void
}

export function createPersonalAssistantEntryActions({ openChat, openContext }: PersonalAssistantEntryDependencies) {
  return {
    context: openContext,
    notification: openChat,
    primary: openChat
  }
}

export async function showPersonalAssistantContext({
  onError,
  refresh,
  setOpen
}: PersonalAssistantContextDependencies): Promise<void> {
  setOpen(true)

  try {
    await refresh()
  } catch (error) {
    setOpen(false)
    onError(error)
  }
}

export async function launchPersonalAssistant(
  trigger: 'manual' | 'scheduled',
  { navigate, refreshState, start }: PersonalAssistantLaunchDependencies
): Promise<{ sessionId?: null | string }> {
  const result = await start(trigger)

  if (result.sessionId) {
    await refreshState()

    if (trigger === 'manual') {
      navigate(sessionRoute(result.sessionId))
    }
  }

  return result
}

export async function openPersonalAssistantTab({
  bindSessionRuntime,
  focusOpenSession,
  openHome,
  openSessionTile
}: PersonalAssistantTabDependencies): Promise<PersonalAssistantDestination> {
  const destination = await openHome()
  const destinationProfile = destination.profile || PERSONAL_ASSISTANT_OWNER_PROFILE
  const alreadyOpen = focusOpenSession(destination.canonicalSessionId)

  openSessionTile(
    destination.canonicalSessionId,
    'center',
    undefined,
    undefined,
    destinationProfile,
    destination.runtimeSessionId
  )
  bindSessionRuntime(destination.canonicalSessionId, destination.runtimeSessionId, destinationProfile)

  if (!alreadyOpen) {
    for (let attempt = 0; attempt < 4; attempt += 1) {
      await new Promise<void>(resolve => setTimeout(resolve, 0))

      if (focusOpenSession(destination.canonicalSessionId)) {
        // The owner gateway can publish its became-open event between the
        // initial bind and the pane's first render, clearing process-local
        // runtime ids. Reapply the live destination after the pane exists.
        await new Promise<void>(resolve => setTimeout(resolve, 100))
        openSessionTile(
          destination.canonicalSessionId,
          'center',
          undefined,
          undefined,
          destinationProfile,
          destination.runtimeSessionId
        )
        bindSessionRuntime(destination.canonicalSessionId, destination.runtimeSessionId, destinationProfile)
        break
      }
    }
  }

  return destination
}

export async function openPersonalAssistantDestination({
  knownSessionId,
  loadKnownSessionId,
  navigate,
  openHome,
  resumeSession
}: PersonalAssistantDestinationDependencies): Promise<PersonalAssistantDestination> {
  // After a restart the browser can restore a deleted route while the durable
  // assistant state already knows the canonical home. Leave that dead route
  // synchronously; waking the owner gateway may take several seconds and must
  // not keep the composer attached to the stale session in the meantime.
  const durableSessionId = knownSessionId || (await loadKnownSessionId?.())

  if (durableSessionId) {
    navigate(sessionRoute(durableSessionId))
  }

  const destination = await openHome()

  // Complete the foreground profile/session rebind before exposing a composer.
  // Otherwise an immediate send can reuse the previous profile's runtime id.
  await resumeSession(destination.canonicalSessionId)
  navigate(sessionRoute(destination.canonicalSessionId))

  return destination
}
