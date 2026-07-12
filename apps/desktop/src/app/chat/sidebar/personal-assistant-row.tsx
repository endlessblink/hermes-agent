import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { SidebarMenuButton, SidebarMenuItem } from '@/components/ui/sidebar'
import type { SessionInfo } from '@/hermes'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { notifyError } from '@/store/notifications'
import {
  $personalAssistantState,
  hydratePersonalAssistantStateWhenReady,
  openPersonalAssistantHome
} from '@/store/personal-assistant'

import type { AppView } from '../../routes'

interface PersonalAssistantSidebarRowProps {
  contentVisible: boolean
  currentView: AppView
  gatewayState: string
  onOpenSession: (sessionId: string) => void
  selectedSessionId: string | null
  sessions: SessionInfo[]
}

export function PersonalAssistantSidebarRow({
  contentVisible,
  currentView,
  gatewayState,
  onOpenSession,
  selectedSessionId,
  sessions
}: PersonalAssistantSidebarRowProps) {
  const { t } = useI18n()
  const state = useStore($personalAssistantState)
  const [opening, setOpening] = useState(false)

  useEffect(() => {
    void hydratePersonalAssistantStateWhenReady(gatewayState).catch(() => undefined)
  }, [gatewayState])

  const selected = useMemo(() => {
    const canonical = state?.sessionId

    if (currentView !== 'chat' || !canonical || !selectedSessionId) {
      return false
    }

    if (selectedSessionId === canonical) {
      return true
    }

    return sessions.some(session => session.id === selectedSessionId && session._lineage_root_id === canonical)
  }, [currentView, selectedSessionId, sessions, state?.sessionId])

  const open = async () => {
    if (opening) {
      return
    }

    setOpening(true)

    try {
      onOpenSession(await openPersonalAssistantHome())
    } catch (error) {
      notifyError(error, t.sidebar.personalAssistantOpenFailed)
    } finally {
      setOpening(false)
    }
  }

  const label = t.sidebar.nav['personal-assistant'] ?? 'Personal assistant'

  return (
    <SidebarMenuItem>
      <SidebarMenuButton
        aria-busy={opening}
        className={cn(
          'flex h-7 w-full justify-start gap-2 rounded-md border border-transparent px-2 text-left text-[0.8125rem] font-medium text-(--ui-text-secondary) transition-colors duration-100 ease-out [-webkit-app-region:no-drag] hover:bg-(--ui-control-hover-background) hover:text-foreground hover:transition-none',
          selected &&
            'border-(--ui-stroke-tertiary) bg-(--ui-control-active-background) text-foreground shadow-none hover:border-(--ui-stroke-tertiary)!'
        )}
        disabled={opening}
        onClick={() => void open()}
        tooltip={label}
        type="button"
      >
        <Codicon className="size-4 shrink-0 text-[color-mix(in_srgb,currentColor_72%,transparent)]" name="sparkle" />
        {contentVisible && (
          <>
            <span className="min-w-0 flex-1 truncate">{label}</span>
            {Boolean(state?.unreadCount) && (
              <span
                aria-label={t.sidebar.personalAssistantUnread(state?.unreadCount ?? 0)}
                className="min-w-4 rounded-full bg-(--ui-accent-background) px-1 text-center text-[0.625rem] font-semibold text-(--ui-accent-foreground)"
              >
                {state?.unreadCount}
              </span>
            )}
          </>
        )}
      </SidebarMenuButton>
    </SidebarMenuItem>
  )
}
