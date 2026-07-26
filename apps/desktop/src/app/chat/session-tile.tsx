/**
 * SESSION TILES — a stored session rendered as a layout-tree pane BESIDE the
 * main thread (multi-session tiling). A tile IS the real chat surface: the
 * same ChatView/ChatBar/Thread tree the primary session renders, mounted
 * under a tile `SessionView` (its session's slice of `$sessionStates`) and a
 * tile `ComposerScope` (own attachment chips, own focus-bus key). Actions
 * (submit/slash/steer/edit/reload/restore/stop) come from
 * `useSessionTileActions`, all writing through the wiring cache.
 *
 * Lifecycle: `openSessionTile(storedId)` -> `watchSessionTiles` registers a
 * pane contribution docked right of the main zone -> tree adoption lands it
 * -> the pane mounts and asks the delegate for a live runtime id. Closing
 * the pane (tab Close) removes the tile + its zone; tiles persist across
 * restarts and re-resume on boot.
 */

import { useStore } from '@nanostores/react'
import { atom, computed } from 'nanostores'
import { useEffect, useMemo, useRef, useState } from 'react'

import { activeRuntimeSessionRow, activeRuntimeSessionStatus } from '@/app/desktop-controller-utils'
import { useGatewayRequest } from '@/app/gateway/hooks/use-gateway-request'
import { blobToDataUrl } from '@/app/session/hooks/use-prompt-actions/utils'
import { formatRefValue } from '@/components/assistant-ui/directive-text'
import { CenteredThreadSpinner } from '@/components/assistant-ui/thread/status'
import { findGroupOfPane } from '@/components/pane-shell/tree/model'
import { $layoutTree, moveTreePane, setTreeGroupHeaderHidden } from '@/components/pane-shell/tree/store'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { transcribeAudio } from '@/hermes'
import { useI18n } from '@/i18n'
import type { ChatMessage } from '@/lib/chat-messages'
import { sessionTitle } from '@/lib/chat-runtime'
import {
  clearClarifyRequestAliasesForSession,
  sessionClarifyRequest,
  setClarifyRequestAliases
} from '@/store/clarify'
import { createComposerAttachmentScope } from '@/store/composer'
import { gatewayForProfile } from '@/store/gateway'
import { $pinnedSessionIds, pinSession, unpinSession } from '@/store/layout'
import { $personalAssistantState, PERSONAL_ASSISTANT_OWNER_PROFILE } from '@/store/personal-assistant'
import { sessionAwaitingInput } from '@/store/prompts'
import {
  $gatewayState,
  $selectedStoredSessionId,
  $sessions,
  sessionMatchesStoredId,
  sessionPinId
} from '@/store/session'
import {
  $sessionStates,
  $sessionTiles,
  closeSessionTile,
  discardSessionTile,
  patchSessionTile,
  type SessionTile,
  sessionTileDelegate
} from '@/store/session-states'

import type { SessionDragPayload } from './composer/inline-refs'
import { type ComposerScope, ComposerScopeProvider } from './composer/scope'
import { useComposerActions } from './hooks/use-composer-actions'
import { paneMirror } from './pane-mirror'
import { startSessionDrag } from './session-drag'
import { useSessionTileActions } from './session-tile-actions'
import { type SessionView, SessionViewProvider } from './session-view'
import { SessionContextMenu } from './sidebar/session-actions-menu'
import { lastVisibleMessageIsUser } from './thread-loading'

import { ChatView } from '.'

const NO_MESSAGES: ChatMessage[] = []

/** The tile's SessionView: the same atom shape the primary chat renders
 *  from, computed from this session's slice of `$sessionStates`. */
function buildTileView(storedSessionId: string): SessionView {
  const $runtimeId = computed(
    $sessionTiles,
    tiles => tiles.find(t => t.storedSessionId === storedSessionId)?.runtimeId ?? null
  )

  const $state = computed([$runtimeId, $sessionStates], (runtimeId, states) =>
    runtimeId ? states[runtimeId] : undefined
  )

  const $messages = computed($state, state => state?.messages ?? NO_MESSAGES)

  return {
    kind: 'tile',
    $awaitingResponse: computed($state, state => Boolean(state?.awaitingResponse)),
    $busy: computed($state, state => Boolean(state?.busy)),
    $cwd: computed($state, state => state?.cwd ?? ''),
    $lastVisibleIsUser: computed($messages, lastVisibleMessageIsUser),
    $messages,
    $messagesEmpty: computed($messages, messages => messages.length === 0),
    $model: computed($state, state => state?.model ?? ''),
    $provider: computed($state, state => state?.provider ?? ''),
    $runtimeId,
    // Constant for the tile's lifetime — a plain atom, not a computed.
    $storedId: atom(storedSessionId)
  }
}

export function latestTranscriptClarifyIsPending(messages: unknown[], question: string): boolean {
  for (let messageIndex = messages.length - 1; messageIndex >= 0; messageIndex -= 1) {
    const message = messages[messageIndex]

    if (!message || typeof message !== 'object') {
      continue
    }

    const content = (message as { content?: unknown }).content

    if (!Array.isArray(content)) {
      continue
    }

    for (let partIndex = content.length - 1; partIndex >= 0; partIndex -= 1) {
      const part = content[partIndex]

      if (!part || typeof part !== 'object') {
        continue
      }

      const row = part as { args?: unknown; result?: unknown; toolName?: unknown; type?: unknown }

      if (row.type !== 'tool-call' || row.toolName !== 'clarify') {
        continue
      }

      let args = row.args

      if (typeof args === 'string') {
        try {
          args = JSON.parse(args)
        } catch {
          args = null
        }
      }

      const askedQuestion =
        args && typeof args === 'object' && typeof (args as { question?: unknown }).question === 'string'
          ? (args as { question: string }).question
          : ''

      return row.result === undefined && askedQuestion === question
    }
  }

  return true
}

function TileChat({
  profile,
  runtimeId,
  storedSessionId,
  view
}: {
  profile?: string
  runtimeId: string
  storedSessionId: string
  view: SessionView
}) {
  const { gatewayRef, requestGateway } = useGatewayRequest()
  const cwd = useStore(view.$cwd)

  // One attachment set + focus key per tile, stable for the tile's lifetime.
  const attachments = useRef(createComposerAttachmentScope()).current

  const tileRequestGateway = useMemo(
    () =>
      profile
        ? async <T,>(
            method: string,
            params: Record<string, unknown> = {},
            timeoutMs?: number,
            signal?: AbortSignal
          ) => {
            const gateway = await gatewayForProfile(profile)

            if (!gateway) {
              throw new Error(`Hermes gateway is unavailable for ${profile}`)
            }

            return gateway.request<T>(method, params, timeoutMs, signal)
          }
        : requestGateway,
    [profile, requestGateway]
  )

  const scope = useMemo<ComposerScope>(
    () => ({
      $clarifyRequest: sessionClarifyRequest(runtimeId),
      $awaitingInput: sessionAwaitingInput(runtimeId),
      attachments,
      popoutAllowed: false,
      readMessages: () => view.$messages.get(),
      requestGateway: tileRequestGateway,
      runtimeSessionId: runtimeId,
      target: `tile:${storedSessionId}`
    }),
    [attachments, runtimeId, storedSessionId, tileRequestGateway, view.$messages]
  )

  useEffect(() => {
    let cancelled = false

    const restorePendingClarify = async () => {
      try {
        const result = await tileRequestGateway<{
          sessions?: Array<{
            id?: string
            pending_prompt?: { choices?: string[]; kind?: string; question?: string; request_id?: string }
            session_key?: string
            status?: string
          }>
        }>('session.active_list', { current_session_id: runtimeId }, 3_000)

        if (cancelled) {
          return
        }

        const row = activeRuntimeSessionRow(result.sessions, runtimeId, storedSessionId)
        const status = activeRuntimeSessionStatus(result.sessions, runtimeId, storedSessionId)
        const pending = row?.pending_prompt

        const transcriptStillWaiting = pending?.question
          ? latestTranscriptClarifyIsPending(view.$messages.get(), pending.question)
          : false

        if (
          status === 'waiting' &&
          transcriptStillWaiting &&
          pending?.kind === 'clarify' &&
          pending.request_id &&
          pending.question
        ) {
          setClarifyRequestAliases(
            {
              choices: Array.isArray(pending.choices) ? pending.choices : null,
              profile,
              question: pending.question,
              requestId: pending.request_id,
              sessionId: runtimeId
            },
            [runtimeId, storedSessionId, row?.id, row?.session_key]
          )
        } else if (!transcriptStillWaiting) {
          clearClarifyRequestAliasesForSession(runtimeId)
          clearClarifyRequestAliasesForSession(storedSessionId)
        }
      } catch {
        // The live gateway event remains the primary path; retry after reconnect.
      }
    }

    void restorePendingClarify()
    const interval = window.setInterval(() => void restorePendingClarify(), 5_000)

    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [runtimeId, storedSessionId, tileRequestGateway, view.$messages])

  const actions = useSessionTileActions({
    profile,
    requestGateway: tileRequestGateway,
    runtimeId,
    scope,
    storedSessionId
  })

  // The same attach/pick/paste/drop pipeline the primary composer uses,
  // pointed at this tile's chips + session.
  const composer = useComposerActions({
    activeSessionId: runtimeId,
    currentCwd: cwd,
    requestGateway: tileRequestGateway,
    scope: { add: attachments.add, remove: attachments.remove, target: scope.target }
  })

  return (
    <SessionViewProvider value={view}>
      <ComposerScopeProvider value={scope}>
        <ChatView
          gateway={gatewayRef.current}
          onAddContextRef={composer.addContextRefAttachment}
          onAddUrl={url => composer.addContextRefAttachment(`@url:${formatRefValue(url)}`, url)}
          onAttachDroppedItems={composer.attachDroppedItems}
          onAttachImageBlob={composer.attachImageBlob}
          onBranchInNewChat={() => undefined}
          onCancel={actions.cancelRun}
          onDeleteSelectedSession={() => undefined}
          onDismissError={actions.dismissError}
          onEdit={actions.editMessage}
          onPasteClipboardImage={opts => composer.pasteClipboardImage(opts)}
          onPickFiles={() => void composer.pickContextPaths('file')}
          onPickFolders={() => void composer.pickContextPaths('folder')}
          onPickImages={() => void composer.pickImages()}
          onReload={actions.reloadFromMessage}
          onRemoveAttachment={id => void composer.removeAttachment(id)}
          onRestoreToMessage={actions.restoreToMessage}
          onRetryResume={() => patchSessionTile(storedSessionId, { error: undefined })}
          onSteer={actions.steerPrompt}
          onSubmit={actions.submitText}
          onThreadMessagesChange={actions.handleThreadMessagesChange}
          onToggleSelectedPin={() => undefined}
          onTranscribeAudio={async audio => (await transcribeAudio(await blobToDataUrl(audio), audio.type)).transcript}
        />
      </ComposerScopeProvider>
    </SessionViewProvider>
  )
}

export function canAttemptSessionTileResume(profile: string | undefined, foregroundGatewayOpen: boolean): boolean {
  return Boolean(profile) || foregroundGatewayOpen
}

export function SessionTilePane({ storedSessionId }: { storedSessionId: string }) {
  const tiles = useStore($sessionTiles)
  const tile = tiles.find(t => t.storedSessionId === storedSessionId)
  const runtimeId = tile?.runtimeId ?? null
  const gatewayOpen = useStore($gatewayState) === 'open'
  const resumingRef = useRef(false)
  const [delegateRevision, setDelegateRevision] = useState(0)
  const view = useMemo(() => buildTileView(storedSessionId), [storedSessionId])

  const canAttemptResume = canAttemptSessionTileResume(tile?.profile, gatewayOpen)

  // Primary-profile tiles share the foreground gateway and must wait for it to
  // open. Profile-owned tiles open their own gateway inside resumeTile; gating
  // those on unrelated foreground state leaves Personal Assistant at
  // "Waking up" forever after a profile switch or restart.
  useEffect(() => {
    if (!canAttemptResume || runtimeId || tile?.error || resumingRef.current) {
      return
    }

    const delegate = sessionTileDelegate()

    if (!delegate) {
      const retry = window.setTimeout(() => setDelegateRevision(revision => revision + 1), 100)

      return () => window.clearTimeout(retry)
    }

    resumingRef.current = true

    delegate
      .resumeTile(storedSessionId, tile?.profile)
      .then(id => patchSessionTile(storedSessionId, { error: undefined, runtimeId: id }))
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : String(err)

        // A gone session (404 / "Session not found") is terminal — a stale or
        // cross-profile persisted tile. Discard it instead of latching an error
        // that re-retries on every reconnect (the "Session not found" spam).
        if (/session not found|\b404\b/i.test(message)) {
          discardSessionTile(storedSessionId)
        } else {
          patchSessionTile(storedSessionId, { error: message })
        }
      })
      .finally(() => {
        resumingRef.current = false
      })
  }, [canAttemptResume, delegateRevision, runtimeId, storedSessionId, tile?.error, tile?.profile])

  // The gateway (re)opening invalidates any latched error — it likely came
  // from a not-yet-open gateway or the previous connection. Clearing it
  // retriggers the resume effect: one bounded auto-retry per (re)connect,
  // mirroring the primary path's became-open resync.
  useEffect(() => {
    if (gatewayOpen && tile?.error) {
      patchSessionTile(storedSessionId, { error: undefined })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gatewayOpen, storedSessionId])

  if (tile?.error) {
    return (
      <div className="grid h-full place-items-center p-4">
        <div className="max-w-[24rem] space-y-2 text-center font-mono text-[11px]">
          <div className="text-(--ui-danger,#f87171)">Couldn't open this session</div>
          <div className="break-words text-(--ui-text-quaternary)">{tile.error}</div>
          <Button onClick={() => patchSessionTile(storedSessionId, { error: undefined })} size="sm" variant="outline">
            Retry
          </Button>
        </div>
      </div>
    )
  }

  if (!runtimeId) {
    // The SAME session loader the primary thread shows (Thread's
    // loading === 'session' branch) — one loading language everywhere.
    return (
      <div className="relative h-full">
        <CenteredThreadSpinner />
      </div>
    )
  }

  return <TileChat profile={tile?.profile} runtimeId={runtimeId} storedSessionId={storedSessionId} view={view} />
}

// ---------------------------------------------------------------------------
// Tile -> pane contribution sync (call once from the app root).
// ---------------------------------------------------------------------------

function tileTitle(storedSessionId: string): string {
  const stored = $sessions.get().find(s => sessionMatchesStoredId(s, storedSessionId))
  const tile = $sessionTiles.get().find(candidate => candidate.storedSessionId === storedSessionId)

  if (
    tile?.profile === PERSONAL_ASSISTANT_OWNER_PROFILE &&
    storedSessionId === $personalAssistantState.get()?.sessionId
  ) {
    return 'Personal assistant'
  }

  return stored ? sessionTitle(stored, $personalAssistantState.get()?.sessionId) : 'Session'
}

/** The `@session` link payload for a tile tab drag — id + owning profile + title. */
function tileDragPayload(storedSessionId: string): SessionDragPayload {
  const stored = $sessions.get().find(s => sessionMatchesStoredId(s, storedSessionId))

  const tile = $sessionTiles.get().find(candidate => candidate.storedSessionId === storedSessionId)

  return { id: storedSessionId, profile: tile?.profile ?? stored?.profile ?? '', title: tileTitle(storedSessionId) }
}

// ---------------------------------------------------------------------------
// Close confirmation — a BUSY tab (streaming, or blocked on clarify/approval
// input) doesn't close silently.
// ---------------------------------------------------------------------------

/** Stored id awaiting close confirmation (null = no dialog). */
const $confirmCloseTile = atom<null | string>(null)

/** The tile closer, gated: a quiet session closes immediately; a busy or
 *  input-blocked one asks first. One state read — the tile's runtime slice. */
export function requestCloseSessionTile(storedSessionId: string): void {
  const runtimeId = $sessionTiles.get().find(t => t.storedSessionId === storedSessionId)?.runtimeId
  const state = runtimeId ? $sessionStates.get()[runtimeId] : undefined

  if (state?.busy || state?.awaitingResponse || state?.needsInput) {
    $confirmCloseTile.set(storedSessionId)
  } else {
    closeSessionTile(storedSessionId)
  }
}

/** Mounted once at the shell root: the "Close running tab?" confirmation. */
export function SessionTileCloseConfirm() {
  const { t } = useI18n()
  const storedSessionId = useStore($confirmCloseTile)

  return (
    <ConfirmDialog
      confirmLabel={t.zones.closeRunningConfirm}
      description={t.zones.closeRunningBody}
      destructive
      onClose={() => $confirmCloseTile.set(null)}
      onConfirm={() => {
        if (storedSessionId) {
          closeSessionTile(storedSessionId)
        }
      }}
      open={storedSessionId !== null}
      title={t.zones.closeRunningTitle}
    />
  )
}

/** Layout reset → every session tile collapses into the MAIN zone as a tab
 *  after the workspace (the primary session stays the first tab), the "smart"
 *  reset: N scattered tiles become one tab bar over the chat instead of
 *  re-docking to their old edges.
 *
 *  Runs BEFORE generic adoption (see registerLayoutResetHandler) — the tiles
 *  aren't in the fresh tree yet, so each `moveTreePane` ADDS the tile into the
 *  workspace group as a tab (append). The main group id is re-read each pass
 *  because appending returns a new tree. */
export function stackSessionTilesIntoMain(): void {
  for (const tile of $sessionTiles.get()) {
    const tree = $layoutTree.get()
    const mainGroup = tree ? findGroupOfPane(tree, 'workspace')?.id : null

    if (mainGroup) {
      moveTreePane(`session-tile:${tile.storedSessionId}`, { groupId: mainGroup, pos: 'center' })
    }
  }
}

/** A session TAB's context menu: the full session verb set (pin, copy id, new
 *  window, branch, rename, archive, delete) — the SAME menu a sidebar row
 *  gets, targeted through the tile delegate (whose verbs are generic over
 *  stored ids, primary included). The wrapper stops the contextmenu from also
 *  opening the zone strip's menu. Shared by tile tabs AND the main tab. */
export function SessionTabMenu({
  children,
  onClose,
  onHideTabBar,
  storedSessionId,
  tabPaneId
}: {
  children: React.ReactElement
  /** Close this tab (tiles; the main tab passes nothing). */
  onClose?: () => void
  /** Hide the zone's tab bar (main tab only — the sticky bar's off switch). */
  onHideTabBar?: () => void
  storedSessionId: string
  /** Layout-tree pane id — powers the Close-others/right/all verbs. */
  tabPaneId: string
}) {
  const sessions = useStore($sessions)
  const pinnedSessionIds = useStore($pinnedSessionIds)
  const stored = sessions.find(s => sessionMatchesStoredId(s, storedSessionId))
  const pinId = stored ? sessionPinId(stored) : storedSessionId
  const pinned = pinnedSessionIds.includes(pinId)

  return (
    <span className="contents" onContextMenu={event => event.stopPropagation()}>
      <SessionContextMenu
        onArchive={() => void sessionTileDelegate()?.archiveSession(storedSessionId)}
        onBranch={() => void sessionTileDelegate()?.branchSession(storedSessionId)}
        onClose={onClose}
        onDelete={() => void sessionTileDelegate()?.deleteSession(storedSessionId)}
        onHideTabBar={onHideTabBar}
        onPin={() => (pinned ? unpinSession(pinId) : pinSession(pinId))}
        pinned={pinned}
        profile={stored?.profile}
        sessionId={storedSessionId}
        surface="tab"
        tabPaneId={tabPaneId}
        title={tileTitle(storedSessionId)}
      >
        {children}
      </SessionContextMenu>
    </span>
  )
}

/** The MAIN tab's menu: the same session verbs targeting the primary's loaded
 *  session, plus the bar's off switch (the bar sticky-shows once a tab is
 *  ever gained; this is the explicit way back). A fresh draft has no session —
 *  no menu. */
export function WorkspaceTabMenu({ children }: { children: React.ReactElement }) {
  const selected = useStore($selectedStoredSessionId)

  const hideTabBar = () => {
    const tree = $layoutTree.get()
    const group = tree ? findGroupOfPane(tree, 'workspace') : null

    if (group) {
      setTreeGroupHeaderHidden(group.id, true)
    }
  }

  if (!selected) {
    return children
  }

  return (
    <SessionTabMenu onHideTabBar={hideTabBar} storedSessionId={selected} tabPaneId="workspace">
      {children}
    </SessionTabMenu>
  )
}

/** Keep pane contributions mirroring `$sessionTiles` (+ titles from
 *  `$sessions`). Tiles dock against main on the chosen edge, flex width. */
export const watchSessionTiles = paneMirror<SessionTile>({
  source: $sessionTiles,
  also: [$sessions, $personalAssistantState],
  key: t => t.storedSessionId,
  prefix: 'session-tile',
  dir: t => t.dir,
  anchor: t => t.anchor,
  before: t => t.before,
  minWidth: '20rem',
  title: tileTitle,
  render: storedSessionId => <SessionTilePane storedSessionId={storedSessionId} />,
  tabWrap: (storedSessionId, tab) => (
    <SessionTabMenu
      onClose={() => requestCloseSessionTile(storedSessionId)}
      storedSessionId={storedSessionId}
      tabPaneId={`session-tile:${storedSessionId}`}
    >
      {tab}
    </SessionTabMenu>
  ),
  // A tile's tab drags like a sidebar row — stack / split / drop-to-link — with
  // its tap (activate) + double-tap (hide bar) preserved. Always takes the drag.
  tabDrag: (storedSessionId, event, onTap, double) => {
    startSessionDrag(tileDragPayload(storedSessionId), event, { double, onTap })

    return true
  },
  close: requestCloseSessionTile
})
