import { sessionRoute } from '@/app/routes'
import type { PersonalAssistantDestination } from '@/store/personal-assistant'

export interface PersonalAssistantDestinationDependencies {
  navigate: (route: string) => void
  openHome: () => Promise<PersonalAssistantDestination>
  resumeSession: (storedSessionId: string) => Promise<void>
}

export async function openPersonalAssistantDestination({
  navigate,
  openHome,
  resumeSession
}: PersonalAssistantDestinationDependencies): Promise<PersonalAssistantDestination> {
  const destination = await openHome()

  // Complete the foreground profile/session rebind before exposing a composer.
  // Otherwise an immediate send can reuse the previous profile's runtime id.
  await resumeSession(destination.canonicalSessionId)
  navigate(sessionRoute(destination.canonicalSessionId))

  return destination
}
