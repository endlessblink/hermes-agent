import { type MutableRefObject, useCallback } from 'react'

import { PROMPT_SUBMIT_REQUEST_TIMEOUT_MS } from '@/hermes'
import type { Translations } from '@/i18n'
import { type ChatMessage, textPart } from '@/lib/chat-messages'
import { optimisticAttachmentRef } from '@/lib/chat-runtime'
import { setMutableRef } from '@/lib/mutable-ref'
import {
  $composerAttachments,
  clearComposerAttachments,
  type ComposerAttachment,
  terminalContextBlocksFromDraft
} from '@/store/composer'
import { clearNotifications, notify, notifyError } from '@/store/notifications'
import { requestDesktopOnboarding } from '@/store/onboarding'
import { $filePreviewTarget } from '@/store/preview'
import { $activeGatewayProfile, normalizeProfileKey } from '@/store/profile'
import { $sessions, clearSessionReplyReady, setAwaitingResponse, setBusy, setMessages } from '@/store/session'

import type { ClientSessionState } from '../../../types'

import { rememberContinuationPrompt } from './continuation-recovery'
import {
  _submitInFlight,
  type GatewayRequest,
  inlineErrorMessage,
  isGatewayTimeoutError,
  isProviderSetupError,
  isSessionBusyError,
  isSessionNotFoundError,
  type SubmitTextOptions,
  withSessionBusyRetry
} from './utils'

interface SubmitPromptDeps {
  activeSessionId: string | null
  activeSessionIdRef: MutableRefObject<string | null>
  busyRef: MutableRefObject<boolean>
  copy: Translations['desktop']
  createBackendSessionForSend: (preview?: string | null) => Promise<string | null>
  ensureSelectedSessionOwner: () => Promise<void>
  requestGateway: GatewayRequest
  selectedStoredSessionIdRef: MutableRefObject<string | null>
  syncAttachmentsForSubmit: (
    sessionId: string,
    attachments: ComposerAttachment[],
    options?: { updateComposerAttachments?: boolean }
  ) => Promise<ComposerAttachment[]>
  updateSessionState: (
    sessionId: string,
    updater: (state: ClientSessionState) => ClientSessionState,
    storedSessionId?: string | null
  ) => ClientSessionState
}

/** The prompt submit pipeline, extracted from usePromptActions. */
export function useSubmitPrompt(deps: SubmitPromptDeps) {
  const {
    activeSessionId,
    activeSessionIdRef,
    busyRef,
    copy,
    createBackendSessionForSend,
    ensureSelectedSessionOwner,
    requestGateway,
    selectedStoredSessionIdRef,
    syncAttachmentsForSubmit,
    updateSessionState
  } = deps

  return useCallback(
    async (rawText: string, options?: SubmitTextOptions) => {
      const visibleText = rawText.trim()
      const usingComposerAttachments = !options?.attachments

      // Drop undefined/null holes a session switch or draft restore can leave in
      // the attachments array (same bug class as AttachmentList #49624). Without
      // this, the sibling iterations below (a.kind / a.label / a.refText, and the
      // sync step) throw "Cannot read properties of undefined (reading 'refText')"
      // and break the chat surface.
      const attachments = (options?.attachments ?? $composerAttachments.get()).filter((a): a is ComposerAttachment =>
        Boolean(a)
      )

      const terminalContextBlocks = terminalContextBlocksFromDraft(rawText).join('\n\n')
      const hasImage = attachments.some(a => a.kind === 'image')

      // Refs are recomputed after sync (file.attach rewrites @file: refs to
      // workspace-relative paths the remote gateway can resolve). Seed the
      // optimistic message with the pre-sync refs, then rewrite once synced.
      // Images use their base64 preview so the thumbnail renders inline without
      // a (remote-mode 403-prone) /api/media fetch — see optimisticAttachmentRef.
      let attachmentRefs = attachments.map(optimisticAttachmentRef).filter((r): r is string => Boolean(r))

      const buildContextText = (atts: ComposerAttachment[]): string => {
        // atts may be the post-sync array, which can reintroduce holes; filter
        // before touching a.refText / a.kind.
        const present = atts.filter((a): a is ComposerAttachment => Boolean(a))

        const contextRefs = present
          .map(a => a.refText)
          .filter(Boolean)
          .join('\n')

        return (
          [contextRefs, terminalContextBlocks, visibleText].filter(Boolean).join('\n\n') ||
          (present.some(a => a.kind === 'image') ? 'What do you see in this image?' : '')
        )
      }

      // Queue drains fire on the busy→false settle edge, where busyRef (synced
      // from $busy by a separate effect) may still read true — honoring it would
      // bounce the drained send. The drain lock serializes them; the user path
      // keeps the guard so a stray Enter mid-turn can't double-submit.
      const hasSendable = Boolean(visibleText || terminalContextBlocks || attachments.length || hasImage)

      if (!hasSendable || (!options?.allowWhileBusy && !options?.fromQueue && busyRef.current)) {
        return false
      }

      // The durable selection owns the gateway route. A profile switch can
      // leave its transcript mounted while another profile socket is active;
      // attachment RPCs happen before prompt.submit recovery, so they would
      // otherwise fail immediately with "session not found".
      try {
        await ensureSelectedSessionOwner()
      } catch (err) {
        notifyError(err, copy.sessionUnavailable)

        return false
      }

      // One submit in flight per session — drop any concurrent re-fire so a
      // stalled turn can't stack the same prompt into multiple real turns.
      const submitLockKey = selectedStoredSessionIdRef.current || activeSessionId || activeSessionIdRef.current || '__pending_new__'

      if (_submitInFlight.has(submitLockKey)) {
        return false
      }

      _submitInFlight.add(submitLockKey)
      let submitLockReleased = false

      const releaseSubmitLock = () => {
        if (!submitLockReleased) {
          submitLockReleased = true
          _submitInFlight.delete(submitLockKey)
        }
      }

      const optimisticId = `user-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`

      const buildUserMessage = (): ChatMessage => ({
        id: optimisticId,
        role: 'user',
        parts: [textPart(visibleText || (attachmentRefs.length ? '' : attachments.map(a => a.label).join(', ')))],
        attachmentRefs,
        hidden: options?.hidden
      })

      const releaseBusy = () => {
        releaseSubmitLock()
        setMutableRef(busyRef, false)
        setBusy(false)
        setAwaitingResponse(false)
      }

      // Idempotent optimistic insert — re-running with the resolved sessionId
      // after createBackendSessionForSend just overwrites with the same id.
      const seedOptimistic = (sid: string) =>
        updateSessionState(
          sid,
          state => ({
            ...state,
            messages: state.messages.some(m => m.id === optimisticId)
              ? state.messages
              : [...state.messages, buildUserMessage()],
            busy: true,
            awaitingResponse: true,
            pendingBranchGroup: null,
            sawAssistantPayload: false,
            // Fresh submit = new turn — clear any leftover interrupt flag, else
            // mutateStream/completeAssistantMessage drop every delta of this turn
            // (what made drained-after-interrupt sends go silent).
            interrupted: false
          }),
          selectedStoredSessionIdRef.current
        )

      // After sync rewrites refs, refresh the optimistic message in place so the
      // transcript shows the resolved @file: ref rather than the local path.
      const rewriteOptimistic = (sid: string) =>
        updateSessionState(
          sid,
          state => ({
            ...state,
            messages: state.messages.map(message => (message.id === optimisticId ? buildUserMessage() : message))
          }),
          selectedStoredSessionIdRef.current
        )

      const dropOptimistic = (sid: null | string) => {
        if (!sid) {
          setMessages(current => current.filter(m => m.id !== optimisticId))

          return
        }

        updateSessionState(
          sid,
          state => ({
            ...state,
            messages: state.messages.filter(m => m.id !== optimisticId),
            busy: false,
            awaitingResponse: false,
            pendingBranchGroup: null
          }),
          selectedStoredSessionIdRef.current
        )
      }

      const resumeParamsForSelectedSession = (
        storedSessionId: string,
        extra?: Record<string, unknown>
      ): Record<string, unknown> => {
        const stored = $sessions.get().find(session => {
          return session.id === storedSessionId || session._lineage_root_id === storedSessionId
        })

        const profile = normalizeProfileKey(stored?.profile ?? $activeGatewayProfile.get())

        return {
          session_id: storedSessionId,
          profile,
          ...extra
        }
      }

      setMutableRef(busyRef, true)
      setBusy(true)
      setAwaitingResponse(true)
      clearSessionReplyReady(selectedStoredSessionIdRef.current ?? activeSessionIdRef.current)
      clearNotifications()

      let sessionId: null | string = activeSessionId || activeSessionIdRef.current

      if (sessionId) {
        seedOptimistic(sessionId)
      } else {
        setMessages(current => [...current, buildUserMessage()])
      }

      if (!sessionId && selectedStoredSessionIdRef.current) {
        // A stored session is SELECTED but its runtime binding is gone (the
        // live session was orphan-reaped, or a timeout/reconnect cleared
        // activeSessionId). Continuing the selected conversation must mean
        // resuming it — minting a brand-new backend session here silently
        // splits the user's chat in two (#55578 symptom b). Only fall through
        // to session creation when NO stored session is selected (a genuine
        // new-chat draft).
        try {
          const resumed = await requestGateway<{ session_id: string }>(
            'session.resume',
            resumeParamsForSelectedSession(selectedStoredSessionIdRef.current)
          )

          if (resumed?.session_id) {
            sessionId = resumed.session_id
            activeSessionIdRef.current = sessionId
          }
        } catch (err) {
          // A selected stored session is a continuity contract. If the durable
          // conversation cannot be resumed, do not mint a fresh chat under the
          // same submit action; that silently splits the user's context.
          dropOptimistic(null)
          releaseBusy()
          notifyError(err, copy.sessionUnavailable)

          return false
        }

        if (sessionId) {
          seedOptimistic(sessionId)
        }
      }

      if (!sessionId) {
        try {
          sessionId = await createBackendSessionForSend(visibleText)
        } catch (err) {
          dropOptimistic(null)
          releaseBusy()
          notifyError(err, copy.sessionUnavailable)

          return false
        }

        if (!sessionId) {
          dropOptimistic(null)
          releaseBusy()
          notify({ kind: 'error', title: copy.sessionUnavailable, message: copy.createSessionFailed })

          return false
        }

        seedOptimistic(sessionId)
      }

      try {
        let syncedAttachments: ComposerAttachment[]

        try {
          syncedAttachments = await syncAttachmentsForSubmit(sessionId, attachments, {
            updateComposerAttachments: usingComposerAttachments
          })
        } catch (attachErr) {
          const storedSessionId = selectedStoredSessionIdRef.current

          if (!storedSessionId || !isSessionNotFoundError(attachErr)) {
            throw attachErr
          }

          // Attachment staging is the first gateway call for image/file sends,
          // so prompt.submit recovery never sees a stale runtime failure. Rebind
          // the durable selected chat and retry the upload exactly once.
          const resumed = await requestGateway<{ session_id?: string }>(
            'session.resume',
            resumeParamsForSelectedSession(storedSessionId, { source: 'desktop' })
          )

          const recoveredId = resumed?.session_id

          if (!recoveredId) {
            throw attachErr
          }

          const staleSessionId = sessionId
          activeSessionIdRef.current = recoveredId

          if (staleSessionId !== recoveredId) {
            dropOptimistic(staleSessionId)
          }

          sessionId = recoveredId
          seedOptimistic(recoveredId)
          syncedAttachments = await syncAttachmentsForSubmit(recoveredId, attachments, {
            updateComposerAttachments: usingComposerAttachments
          })
        }

        // Rewrite the optimistic message + prompt text with the synced refs so
        // the gateway receives @file: paths that resolve in its workspace.
        // (Images keep their inline base64 preview — see optimisticAttachmentRef.)
        attachmentRefs = syncedAttachments.map(optimisticAttachmentRef).filter((r): r is string => Boolean(r))
        rewriteOptimistic(sessionId)
        const text = buildContextText(syncedAttachments)

        // On sleep/wake the gateway's in-memory session may have been cleared
        // while the desktop app still holds the old session ID. Detect this and
        // retry once: resume durable stored sessions, or create a fresh runtime
        // session when the user is composing from a new draft.
        let submitErr: unknown = null

        // The artifact currently shown in the active preview tab. Sent each turn
        // so the backend can bind deictic references ("animate this") to it
        // instead of the model guessing from history/memory. `source` lets the
        // backend distrust a preview that auto-followed a generation result.
        const previewTarget = $filePreviewTarget.get()

        const activeTarget = previewTarget
          ? {
              kind: previewTarget.kind,
              path: previewTarget.path,
              url: previewTarget.url,
              label: previewTarget.label,
              source: previewTarget.source,
            }
          : undefined

        try {
          rememberContinuationPrompt(sessionId, text)
          await withSessionBusyRetry(() =>
            requestGateway('prompt.submit', { session_id: sessionId, text, active_target: activeTarget }, PROMPT_SUBMIT_REQUEST_TIMEOUT_MS)
          )
        } catch (firstErr) {
          if (isSessionNotFoundError(firstErr) || isGatewayTimeoutError(firstErr)) {
            const storedSessionId = selectedStoredSessionIdRef.current

            if (storedSessionId) {
              let recoveredId: null | string = null

              try {
                // Re-register the session in the gateway and get a fresh live ID.
                // Timeouts recover the same way as "session not found": a starved
                // backend loop (#55578 symptom d) rejects the submit even though
                // the stored session is fine — resume + retry instead of erroring
                // out and losing the session binding.
                const resumed = await requestGateway<{ session_id: string }>(
                  'session.resume',
                  resumeParamsForSelectedSession(storedSessionId, { source: 'desktop' })
                )

                recoveredId = resumed?.session_id || null
              } catch (resumeErr) {
                // A selected stored session must either resume or fail visibly.
                // Creating a fresh replacement here makes the UI look like the
                // same chat while the backend has no prior context.
                submitErr = resumeErr
              }

              if (!recoveredId && submitErr === null && !options?.fromQueue) {
                recoveredId = await createBackendSessionForSend(visibleText)
              }

              if (recoveredId) {
                const staleSessionId = sessionId

                activeSessionIdRef.current = recoveredId

                if (staleSessionId !== recoveredId) {
                  dropOptimistic(staleSessionId)
                }

                sessionId = recoveredId
                seedOptimistic(recoveredId)
                rewriteOptimistic(recoveredId)
                rememberContinuationPrompt(recoveredId, text)
                await withSessionBusyRetry(() =>
                  requestGateway('prompt.submit', { session_id: recoveredId, text, active_target: activeTarget }, PROMPT_SUBMIT_REQUEST_TIMEOUT_MS)
                )
              } else if (submitErr === null) {
                submitErr = firstErr
              }
            } else if (!options?.fromQueue) {
              const staleSessionId = sessionId
              const recoveredId = await createBackendSessionForSend(visibleText)

              if (recoveredId) {
                activeSessionIdRef.current = recoveredId

                if (staleSessionId !== recoveredId) {
                  dropOptimistic(staleSessionId)
                }

                sessionId = recoveredId
                seedOptimistic(recoveredId)
                rewriteOptimistic(recoveredId)
                rememberContinuationPrompt(recoveredId, text)
                await withSessionBusyRetry(() =>
                  requestGateway('prompt.submit', { session_id: recoveredId, text, active_target: activeTarget }, PROMPT_SUBMIT_REQUEST_TIMEOUT_MS)
                )
              } else {
                submitErr = firstErr
              }
            } else {
              submitErr = firstErr
            }
          } else {
            submitErr = firstErr
          }
        }

        if (submitErr !== null) {
          throw submitErr
        }

        if (usingComposerAttachments) {
          clearComposerAttachments()
        }

        // Submit landed — the turn now runs (busy stays true), but the submit
        // window is closed, so release the lock for the next (sequential) send.
        releaseSubmitLock()

        return true
      } catch (err) {
        // A send that never made it past the gateway's concurrency guard should
        // not become transcript history. Remove the optimistic user bubble and
        // report "not accepted" so the composer restores the draft or keeps the
        // queued entry for the next idle drain.
        if (isSessionBusyError(err)) {
          releaseBusy()
          dropOptimistic(sessionId)

          return false
        }

        releaseBusy()

        const message = inlineErrorMessage(err, copy.promptFailed)

        updateSessionState(sessionId, state => ({
          ...state,
          messages: [
            ...state.messages,
            {
              id: `assistant-error-${Date.now()}`,
              role: 'assistant',
              parts: [],
              error: message || copy.promptFailed,
              branchGroupId: state.pendingBranchGroup ?? undefined
            }
          ],
          busy: false,
          awaitingResponse: false,
          pendingBranchGroup: null,
          sawAssistantPayload: true
        }))

        if (isProviderSetupError(err)) {
          requestDesktopOnboarding(copy.providerCredentialRequired)

          return false
        }

        notifyError(err, copy.promptFailed)

        return false
      }
    },
    [
      activeSessionId,
      activeSessionIdRef,
      busyRef,
      copy,
      createBackendSessionForSend,
      ensureSelectedSessionOwner,
      requestGateway,
      selectedStoredSessionIdRef,
      syncAttachmentsForSubmit,
      updateSessionState
    ]
  )
}
