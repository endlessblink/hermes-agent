import { type RefObject, useEffect, useRef } from 'react'

import { SLASH_COMMAND_RE } from '@/lib/chat-runtime'
import { triggerHaptic } from '@/lib/haptics'
import { clearSessionDraft, type ComposerAttachment } from '@/store/composer'
import { resetBrowseState } from '@/store/composer-input-history'
import { enqueueQueuedPrompt } from '@/store/composer-queue'

import { cloneAttachments, type QueueEditState } from '../composer-utils'
import { onComposerSubmitRequest } from '../focus'
import { composerPlainText } from '../rich-editor'
import { useComposerScope } from '../scope'
import type { ChatBarProps } from '../types'

interface UseComposerSubmitArgs {
  activeQueueSessionKey: string | null
  activeQueueSessionKeyRef: RefObject<string | null>
  attachments: ComposerAttachment[]
  busy: boolean
  canSteer: boolean
  clearDraft: () => void
  disabled: boolean
  draftRef: RefObject<string>
  drainNextQueued: () => Promise<boolean>
  editorRef: RefObject<HTMLDivElement | null>
  exitQueuedEdit: (action: 'cancel' | 'save') => boolean
  focusInput: () => void
  implicitQueueDrainAllowed: () => boolean
  inputDisabled: boolean
  loadIntoComposer: (text: string, attachments: ComposerAttachment[]) => void
  onCancel: ChatBarProps['onCancel']
  onSteer: ChatBarProps['onSteer']
  onSubmit: ChatBarProps['onSubmit']
  onSubmitAccepted?: () => void
  onSubmitClarifyAnswer?: (answer: string) => Promise<boolean | 'stale'> | boolean
  queueCurrentDraft: () => boolean
  queueEdit: QueueEditState | null
  recoverLostClarifyWhileBusy: boolean
  sendBlocked: boolean
  sessionId: string | null | undefined
  setComposerText: (value: string) => void
  stashAt: (scope: string | null, text?: string, attachments?: ComposerAttachment[]) => void
  transformSubmitText?: (text: string) => string
}

/**
 * The composer's submit engine — the orchestration seam where the draft and
 * queue meet. `submitDraft` is the one decision tree (queue-edit save · slash-
 * now-while-busy · queue · drain · send · stop); `dispatchSubmit` is the shared
 * send-with-restore primitive (re-loads + re-stashes the draft if the gateway
 * rejects, so nothing is ever lost); `steerDraft` nudges the live turn. Reads
 * the draft + queue APIs; owns no state of its own beyond the stable
 * external-submit listener ref.
 */
export function useComposerSubmit({
  activeQueueSessionKey,
  activeQueueSessionKeyRef,
  attachments,
  busy,
  canSteer,
  clearDraft,
  disabled,
  draftRef,
  drainNextQueued,
  editorRef,
  exitQueuedEdit,
  focusInput,
  implicitQueueDrainAllowed,
  inputDisabled,
  loadIntoComposer,
  onCancel,
  onSteer,
  onSubmit,
  onSubmitAccepted,
  onSubmitClarifyAnswer,
  queueCurrentDraft,
  queueEdit,
  recoverLostClarifyWhileBusy,
  sendBlocked,
  sessionId,
  setComposerText,
  stashAt,
  transformSubmitText
}: UseComposerSubmitArgs) {
  const scope = useComposerScope()

  // Shared send primitive: fire onSubmit, and if the gateway rejects (accepted
  // === false) or throws, re-load + re-stash the draft so the words survive.
  const dispatchSubmit = (
    text: string,
    options?: { allowWhileBusy?: boolean; attachments?: ComposerAttachment[]; hidden?: boolean }
  ) => {
    const submittedScope = activeQueueSessionKeyRef.current
    const submittedAttachments = options?.attachments ?? []
    const submittedText = transformSubmitText?.(text) ?? text

    const restore = () => {
      loadIntoComposer(text, submittedAttachments)
      // Use the scope captured at dispatch, not whatever session is focused
      // now — the gateway can reject well after the user has switched away,
      // and re-stashing into the currently-focused session would overwrite
      // its draft with the rejected text from a different session (#54527).
      stashAt(submittedScope, text, submittedAttachments)
    }

    void Promise.resolve(onSubmit(submittedText, options))
      .then(accepted => {
        if (accepted === false) {
          if (!options?.hidden) {
            restore()
          }
        } else {
          clearSessionDraft(submittedScope)
          onSubmitAccepted?.()
        }
      })
      .catch(() => {
        if (!options?.hidden) {
          restore()
        }
      })
  }

  // External "submit this prompt" requests (e.g. the review pane's agent-ship
  // button) route through the same send path. A ref keeps the listener stable
  // while always calling the latest dispatchSubmit closure.
  const dispatchSubmitRef = useRef(dispatchSubmit)
  dispatchSubmitRef.current = dispatchSubmit

  const dispatchClarifyAnswer = (text: string) => {
    if (!onSubmitClarifyAnswer) {
      return false
    }

    const submittedScope = activeQueueSessionKeyRef.current

    const restore = () => {
      loadIntoComposer(text, [])
      stashAt(activeQueueSessionKeyRef.current, text, [])
    }

    clearDraft()

    void Promise.resolve(onSubmitClarifyAnswer(text.trim()))
      .then(accepted => {
        if (accepted === 'stale') {
          // The pending clarify no longer exists on the gateway — the stale
          // request was just cleared. Re-route the SAME text through the
          // normal busy path (queue with auto-drain) so the user's message
          // still lands without a second click.
          restore()
          queueCurrentDraft()
        } else if (accepted === false) {
          restore()
        } else {
          clearSessionDraft(submittedScope)
          onSubmitAccepted?.()
        }
      })
      .catch(restore)

    return true
  }

  useEffect(
    () =>
      onComposerSubmitRequest(({ allowWhileBusy, hidden, target, text }) => {
        if (target === 'main' && !inputDisabled) {
          dispatchSubmitRef.current(text, allowWhileBusy || hidden ? { allowWhileBusy, hidden } : undefined)
        }
      }),
    [inputDisabled]
  )

  const submitDraft = () => {
    if (disabled) {
      return
    }

    // Source the text from the DOM editor, not React state. The AUI composer
    // state (`draft`) and the derived `hasComposerPayload` lag the DOM by a
    // render, so on fast typing or IME composition the final keystroke(s) may
    // not have synced yet — reading state here drops the message (Enter looks
    // like it does nothing; typing a trailing space only "fixes" it because the
    // extra input event forces a state sync). draftRef is updated on every
    // input event; refresh it from the editor once more to also cover an
    // in-flight keystroke that hasn't fired its input event yet.
    const editor = editorRef.current

    if (editor) {
      const domText = composerPlainText(editor)

      if (domText !== draftRef.current) {
        draftRef.current = domText
        setComposerText(domText)
      }
    }

    const text = draftRef.current
    const payloadPresent = text.trim().length > 0 || attachments.length > 0

    if (queueEdit) {
      exitQueuedEdit('save')
    } else if (sendBlocked) {
      // Slash commands should execute immediately even while the agent is
      // busy — they're client-side operations (/yolo, /skin, /new, /help,
      // etc.) or self-contained gateway RPCs (/status, /compress).  onSubmit
      // routes them to executeSlashCommand, which has its own per-command
      // busy guard for commands that genuinely need an idle session (skill
      // /send directives).  Queuing them would make every slash command wait
      // for the current turn to finish, which is how the TUI never behaves.
      if (onSubmitClarifyAnswer && payloadPresent && !attachments.length && text.trim()) {
        dispatchClarifyAnswer(text)
      } else if (recoverLostClarifyWhileBusy && busy && !attachments.length && text.trim()) {
        // A reconnect can lose the renderer's one-shot clarify.request while
        // the Personal Assistant backend remains blocked waiting for it. Send
        // the typed answer through prompt.submit instead of the local queue;
        // the gateway's PA-only busy handler resolves a pending clarify, and
        // otherwise retains its normal interrupt/next-turn behavior.
        triggerHaptic('submit')
        resetBrowseState(sessionId)
        clearDraft()
        dispatchSubmit(text.trim(), { allowWhileBusy: true })
      } else if (busy && !attachments.length && SLASH_COMMAND_RE.test(text.trim())) {
        triggerHaptic('submit')
        clearDraft()
        dispatchSubmit(text)
      } else if (payloadPresent) {
        queueCurrentDraft()
      } else {
        // Stop button (the only way to reach here while truly busy with an
        // empty composer — empty Enter is short-circuited in the keydown
        // handler). Compaction without payload has nothing to submit/cancel.
        if (busy) {
          triggerHaptic('cancel')
          void Promise.resolve(onCancel())
        }
      }
    } else if (!payloadPresent && implicitQueueDrainAllowed()) {
      void drainNextQueued()
    } else if (payloadPresent) {
      const submittedAttachments = cloneAttachments(attachments)
      triggerHaptic('submit')
      resetBrowseState(sessionId)
      clearDraft()
      scope.attachments.clear()
      dispatchSubmit(text, { attachments: submittedAttachments })
    }

    focusInput()
  }

  // Steer the live turn (nudge without interrupting). Clears the draft up front
  // for snappy feedback; if the gateway rejects (no live tool window) the words
  // are re-queued so nothing is lost — same safety net as a plain queue.
  const steerDraft = () => {
    if (!onSteer || !canSteer) {
      return
    }

    const text = draftRef.current.trim()

    triggerHaptic('submit')
    clearDraft()

    void Promise.resolve(onSteer(text)).then(accepted => {
      if (!accepted && activeQueueSessionKey) {
        enqueueQueuedPrompt(activeQueueSessionKey, { text, attachments: [], autoDrain: true })
      }
    })
  }

  return { dispatchSubmit, steerDraft, submitDraft }
}
