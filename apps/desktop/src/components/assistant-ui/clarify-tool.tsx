'use client'

import { type ToolCallMessagePartProps, useAuiState } from '@assistant-ui/react'
import { useStore } from '@nanostores/react'
import {
  type ComponentProps,
  type FormEvent,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState
} from 'react'

import { requestComposerSubmit } from '@/app/chat/composer/focus'
import { useComposerScope } from '@/app/chat/composer/scope'
import { useSessionView } from '@/app/chat/session-view'
import { ToolFallback } from '@/components/assistant-ui/tool/fallback'
import { Button } from '@/components/ui/button'
import { Kbd } from '@/components/ui/kbd'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n'
import { respondToClarifyRequest } from '@/lib/clarify-response'
import { CircleLetterA, Loader2, MessageQuestion } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { sessionClarifyRequest } from '@/store/clarify'
import { $gateway, gatewayForProfile } from '@/store/gateway'

import { selectMessageRunning } from './tool/fallback-model'
import { parseMaybeObject } from './tool/fallback-model/format'

interface ClarifyArgs {
  question?: string
  choices?: string[] | null
}

interface ClarifyResult {
  question?: string
  answer?: string
  error?: string
}

type ClarifyRequestGateway = <T>(method: string, params: Record<string, unknown>) => Promise<T>

export function clarifyRequestGateway(
  profile: string | undefined,
  fallback: ClarifyRequestGateway | undefined,
  resolveProfileGateway = gatewayForProfile
): ClarifyRequestGateway | undefined {
  if (!profile) {
    return fallback
  }

  return async <T,>(method: string, params: Record<string, unknown>) => {
    const gateway = await resolveProfileGateway(profile)

    if (!gateway) {
      throw new Error(`Hermes gateway is unavailable for ${profile}`)
    }

    return gateway.request<T>(method, params)
  }
}

export function clarifyRecoveryMessage(question: string, answer: string): string {
  const response = answer.trim() || 'דלג'

  return `תשובה לשאלה ״${question}״: ${response}. המשך מאותה נקודה ואל תשאל שוב את אותה שאלה.`
}

function stringField(row: Record<string, unknown>, ...keys: string[]): string | undefined {
  for (const key of keys) {
    const value = row[key]

    if (typeof value === 'string') {
      return value
    }
  }
}

function readClarifyArgs(args: unknown): ClarifyArgs {
  const row = parseMaybeObject(args)
  const choices = Array.isArray(row.choices) ? row.choices.filter((c): c is string => typeof c === 'string') : null

  return {
    question: stringField(row, 'question'),
    choices: choices && choices.length > 0 ? choices : null
  }
}

/** Parse clarify tool JSON (`question` + `user_response`). */
export function readClarifyResult(result: unknown): ClarifyResult {
  const row = parseMaybeObject(result)

  if (Object.keys(row).length === 0) {
    return typeof result === 'string' && result.trim() ? { answer: result.trim() } : {}
  }

  return {
    question: stringField(row, 'question'),
    answer: stringField(row, 'user_response', 'answer'),
    error: stringField(row, 'error')
  }
}

const letterFor = (index: number): string => String.fromCharCode(65 + index)

const OPTION_ROW_CLASS =
  'flex w-full items-start gap-2 rounded-[0.25rem] px-1.5 py-1 text-start disabled:cursor-not-allowed disabled:opacity-50'

const RTL_TEXT_RE = /[\u0590-\u05ff\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]/

export function clarifyDirection(question: string, choices: readonly string[]): 'ltr' | 'rtl' {
  return RTL_TEXT_RE.test([question, ...choices].join('\n')) ? 'rtl' : 'ltr'
}

// field-sizing on top of Textarea's shared chrome; kill min-h-16 for one-liners.
const CLARIFY_TEXTAREA_CLASS = 'field-sizing-content max-h-40 min-h-0 resize-none'

const CLARIFY_SHELL_CLASS =
  'my-1.5 rounded-md border border-primary/20 bg-(--ui-chat-surface-background) text-[length:var(--conversation-text-font-size)] text-(--ui-text-primary)'

const CLARIFY_ICON_CLASS = 'mt-px size-4 shrink-0 text-(--ui-text-tertiary)'

function ClarifyShell({ children, className, ...props }: ComponentProps<'div'>) {
  return (
    <div className={cn(CLARIFY_SHELL_CLASS, className)} data-slot="clarify-inline" {...props}>
      {children}
    </div>
  )
}

function ClarifyLine({
  children,
  className,
  icon: Icon,
  ...props
}: ComponentProps<'div'> & { icon: typeof MessageQuestion }) {
  return (
    <div className={cn('flex items-start gap-2', className)} {...props}>
      <div className="min-w-0 flex-1">{children}</div>
      <Icon aria-hidden className={CLARIFY_ICON_CLASS} />
    </div>
  )
}

function KeyBadge({ char, preview, selected }: { char: string; preview?: boolean; selected: boolean }) {
  return (
    <Kbd
      className={cn(
        'mt-px',
        selected && 'border-primary bg-primary text-primary-foreground shadow-none',
        !selected && preview && 'border-primary text-primary shadow-none'
      )}
      size="sm"
    >
      {char}
    </Kbd>
  )
}

function useSessionScopedClarifyRequest() {
  const sessionId = useStore(useSessionView().$runtimeId)
  const $request = useMemo(() => sessionClarifyRequest(sessionId), [sessionId])

  return useStore($request)
}

export const ClarifyTool = (props: ToolCallMessagePartProps) => {
  const request = useSessionScopedClarifyRequest()
  const fromArgs = useMemo(() => readClarifyArgs(props.args), [props.args])
  const latestMatchingPendingToolCallId = useAuiState(state => {
    if (!request?.question) {
      return null
    }

    for (let messageIndex = state.thread.messages.length - 1; messageIndex >= 0; messageIndex -= 1) {
      const content = state.thread.messages[messageIndex]?.content

      if (!Array.isArray(content)) {
        continue
      }

      for (let partIndex = content.length - 1; partIndex >= 0; partIndex -= 1) {
        const part = content[partIndex]

        if (
          part?.type === 'tool-call' &&
          part.toolName === 'clarify' &&
          part.result === undefined &&
          readClarifyArgs(part.args).question === request.question
        ) {
          return part.toolCallId
        }
      }
    }

    return null
  })

  const requestStillWaiting = Boolean(
    request?.question &&
      (!fromArgs.question || fromArgs.question === request.question) &&
      (!latestMatchingPendingToolCallId || props.toolCallId === latestMatchingPendingToolCallId)
  )

  if (
    props.result !== undefined &&
    latestMatchingPendingToolCallId &&
    props.toolCallId !== latestMatchingPendingToolCallId
  ) {
    return null
  }

  // Answered → settled Q&A (ToolFallback collapsed the answer away).
  // A tool.complete can race ahead of the authoritative clarify request; the
  // request wins until the user actually answers it.
  if (props.result !== undefined && !requestStillWaiting) {
    return <ClarifyToolSettled {...props} />
  }

  return <ClarifyToolLive {...props} />
}

function ClarifyToolLive(props: ToolCallMessagePartProps) {
  const messageRunning = useAuiState(selectMessageRunning)
  const messageId = useAuiState(state => state.message.id)
  const request = useSessionScopedClarifyRequest()
  const fromArgs = useMemo(() => readClarifyArgs(props.args), [props.args])

  const latestUnansweredClarifyMessageId = useAuiState(state => {
    for (let index = state.thread.messages.length - 1; index >= 0; index -= 1) {
      const message = state.thread.messages[index]
      const content = Array.isArray(message.content) ? message.content : []
      const hasUnansweredClarify = content.some(
        part => part.type === 'tool-call' && part.toolName === 'clarify' && part.result === undefined
      )

      if (hasUnansweredClarify) {
        return message.id
      }
    }

    return null
  })

  const latestMatchingClarifyMessageId = useAuiState(state => {
    if (!request?.question) {
      return null
    }

    for (let index = state.thread.messages.length - 1; index >= 0; index -= 1) {
      const message = state.thread.messages[index]
      const content = Array.isArray(message.content) ? message.content : []

      const matches = content.some(part => {
        if (part.type !== 'tool-call' || part.toolName !== 'clarify') {
          return false
        }

        return readClarifyArgs(part.args).question === request.question
      })

      if (matches) {
        return message.id
      }
    }

    return null
  })

  // The gateway can emit tool.complete before the renderer has received or
  // reconciled the matching clarify.request. A pending request in the store is
  // the source of truth for "the agent is blocked on user input"; keep the
  // interactive panel visible until that request is answered or cleared.
  const hasLiveMatchingRequest = Boolean(
    request &&
    messageId === latestMatchingClarifyMessageId &&
    (!fromArgs.question || !request.question || fromArgs.question === request.question)
  )

  const isSupersededMatchingRequest = Boolean(
    request?.question &&
    fromArgs.question === request.question &&
    latestMatchingClarifyMessageId &&
    messageId !== latestMatchingClarifyMessageId
  )

  const isPending =
    !isSupersededMatchingRequest && ((messageRunning && props.result === undefined) || hasLiveMatchingRequest)

  if (!isPending) {
    const canRecoverAfterRestart = Boolean(
      props.result === undefined &&
        !messageRunning &&
        !request &&
        fromArgs.question &&
        messageId === latestUnansweredClarifyMessageId
    )

    if (canRecoverAfterRestart) {
      return <ClarifyToolRecovery args={fromArgs} />
    }

    return <ToolFallback {...props} />
  }

  return <ClarifyToolPending {...props} />
}

function ClarifyToolRecovery({ args }: { args: ClarifyArgs }) {
  const { target } = useComposerScope()
  const question = args.question ?? ''
  const choices = args.choices ?? []
  const direction = clarifyDirection(question, choices)
  const [draft, setDraft] = useState('')

  const submit = (answer: string) => {
    requestComposerSubmit(clarifyRecoveryMessage(question, answer), { target })
  }

  return (
    <ClarifyShell className="grid gap-2 px-2.5 py-2" data-slot="clarify-recovery" dir={direction}>
      <div className="flex items-start gap-2">
        <span className="flex-1 whitespace-pre-wrap font-medium" data-bidi-plaintext="" dir="auto">
          {question}
        </span>
        <MessageQuestion aria-hidden className={CLARIFY_ICON_CLASS} />
      </div>
      {choices.length > 0 ? (
        <div className="grid gap-px">
          {choices.map((choice, index) => (
            <button className={OPTION_ROW_CLASS} key={`${index}-${choice}`} onClick={() => submit(choice)} type="button">
              <KeyBadge char={letterFor(index)} selected={false} />
              <span className="flex-1 wrap-anywhere" data-bidi-plaintext="" dir="auto">
                {choice}
              </span>
            </button>
          ))}
        </div>
      ) : null}
      <form
        className="flex items-end gap-1"
        onSubmit={event => {
          event.preventDefault()
          if (draft.trim()) {
            submit(draft)
          }
        }}
      >
        <Textarea
          className={CLARIFY_TEXTAREA_CLASS}
          dir={direction}
          onChange={event => setDraft(event.target.value)}
          placeholder={direction === 'rtl' ? 'תשובה אחרת' : 'Other answer'}
          rows={1}
          size="sm"
          value={draft}
        />
        <Button disabled={!draft.trim()} size="xs" type="submit">
          {direction === 'rtl' ? 'המשך' : 'Continue'}
        </Button>
        <Button onClick={() => submit('')} size="xs" type="button" variant="text">
          {direction === 'rtl' ? 'דלג' : 'Skip'}
        </Button>
      </form>
    </ClarifyShell>
  )
}

function ClarifyToolSettled({ args, result }: ToolCallMessagePartProps) {
  const { t } = useI18n()
  const copy = t.assistant.clarify
  const fromArgs = useMemo(() => readClarifyArgs(args), [args])
  const fromResult = useMemo(() => readClarifyResult(result), [result])

  const question = fromResult.question || fromArgs.question || ''
  const answer = fromResult.answer
  const error = fromResult.error
  const skipped = !error && answer !== undefined && !answer.trim()
  const direction = clarifyDirection(question, answer ? [answer] : [])
  const answerText = error || (skipped ? (direction === 'rtl' ? 'דולג' : copy.skipped) : (answer ?? '').trim())

  return (
    <ClarifyShell className="grid gap-1.5 px-2.5 py-2" data-clarify-settled="" dir={direction}>
      {question ? (
        <ClarifyLine icon={MessageQuestion}>
          <span className="whitespace-pre-wrap font-medium leading-(--conversation-line-height)">{question}</span>
        </ClarifyLine>
      ) : null}
      {answerText ? (
        <ClarifyLine icon={CircleLetterA}>
          <p
            className={cn(
              'whitespace-pre-wrap leading-(--conversation-line-height)',
              error ? 'text-destructive' : 'text-(--ui-text-secondary)',
              skipped && 'italic text-(--ui-text-tertiary)'
            )}
            data-clarify-answer=""
          >
            {answerText}
          </p>
        </ClarifyLine>
      ) : null}
    </ClarifyShell>
  )
}

function ClarifyToolPending({ args }: ToolCallMessagePartProps) {
  const { t } = useI18n()
  const copy = t.assistant.clarify
  // The tool row is in whichever session's transcript rendered it — read THAT
  // session's clarify (primary or tile), not the globally-active one.
  const sessionId = useStore(useSessionView().$runtimeId)
  const scope = useComposerScope()
  const $request = useMemo(() => sessionClarifyRequest(sessionId), [sessionId])
  const request = useStore($request)
  const gateway = useStore($gateway)
  const fromArgs = useMemo(() => readClarifyArgs(args), [args])

  const matchingRequest = useMemo(() => {
    if (!request) {
      return null
    }

    if (fromArgs.question && request.question && fromArgs.question !== request.question) {
      return null
    }

    return request
  }, [fromArgs.question, request])

  const question = fromArgs.question || matchingRequest?.question || ''

  const choices = useMemo(
    () => fromArgs.choices ?? matchingRequest?.choices ?? [],
    [fromArgs.choices, matchingRequest?.choices]
  )

  const hasChoices = choices.length > 0
  const direction = clarifyDirection(question, choices)
  const rtl = direction === 'rtl'
  const actionCopy = rtl ? { continueLabel: 'המשך', other: 'תשובה אחרת', placeholder: 'כתבו תשובה', skip: 'דלג' } : copy

  const [draft, setDraft] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [selectedChoices, setSelectedChoices] = useState<string[]>([])
  const [otherFocused, setOtherFocused] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const requestGateway = useMemo(
    () => clarifyRequestGateway(matchingRequest?.profile, scope.requestGateway),
    [matchingRequest?.profile, scope.requestGateway]
  )

  // Race: tool.start fires a tick before clarify.request, so request_id
  // arrives slightly after the tool block mounts. Hold the whole panel on a
  // spinner until the gateway request is wired — showing disabled choices or
  // a "loading question" stub is worse than a brief wait.
  const ready = Boolean(matchingRequest?.requestId)
  const loading = !ready && !submitting

  const respond = useCallback(
    async (answer: string, options?: { allowEmpty?: boolean }) => {
      await respondToClarifyRequest({
        allowEmpty: options?.allowEmpty,
        answer,
        copy,
        gateway,
        onBeforeSend: () => setSubmitting(true),
        onError: () => setSubmitting(false),
        request: ready ? matchingRequest : null,
        requestGateway
      })
      // The matching tool.complete will land shortly after, swapping this panel
      // for the ToolFallback view above.
    },
    [copy, gateway, matchingRequest, ready, requestGateway]
  )

  const trimmedDraft = draft.trim()
  // The answer is whichever input is active: all picked choices, or typed text.
  // Choices toggle independently and are submitted together, one per line.
  const pendingAnswer = selectedChoices.length > 0 ? selectedChoices.join('\n') : trimmedDraft || null

  const selectChoice = useCallback((choice: string) => {
    // Picking choices and typing are mutually exclusive answers.
    setDraft('')
    setSelectedChoices(current =>
      current.includes(choice) ? current.filter(selected => selected !== choice) : [...current, choice]
    )
  }, [])

  const submitAnswer = useCallback(() => {
    if (selectedChoices.length > 0) {
      void respond(selectedChoices.join('\n'))

      return
    }

    if (trimmedDraft) {
      void respond(trimmedDraft)
    }
  }, [respond, selectedChoices, trimmedDraft])

  const handleTextareaKey = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.nativeEvent.isComposing) {
        return
      }

      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault()
        submitAnswer()
      }
    },
    [submitAnswer]
  )

  const handleSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault()
      submitAnswer()
    },
    [submitAnswer]
  )

  // Letter shortcuts: A/B/C… pick the matching option, the trailing letter jumps
  // into "Other", and Enter confirms the current pick. Stands down whenever a
  // field is focused (you're typing, not navigating) so it never eats keystrokes
  // meant for the composer or the Other box.
  useEffect(() => {
    if (!ready || !hasChoices || submitting) {
      return
    }

    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey || event.defaultPrevented) {
        return
      }

      const active = document.activeElement as HTMLElement | null

      if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable)) {
        return
      }

      const key = event.key.toLowerCase()

      if (key.length === 1 && key >= 'a' && key <= 'z') {
        const index = key.charCodeAt(0) - 97

        if (index < choices.length) {
          event.preventDefault()
          selectChoice(choices[index])
        } else if (index === choices.length) {
          event.preventDefault()
          textareaRef.current?.focus()
        }

        return
      }

      if (event.key === 'Enter' && pendingAnswer) {
        event.preventDefault()
        submitAnswer()
      }
    }

    window.addEventListener('keydown', onKeyDown)

    return () => window.removeEventListener('keydown', onKeyDown)
  }, [choices, hasChoices, pendingAnswer, ready, selectChoice, submitAnswer, submitting])

  if (loading) {
    return (
      <ClarifyShell
        aria-label={copy.loadingQuestion}
        className="grid min-h-12 place-items-center px-2.5 py-3"
        dir={direction}
        role="status"
      >
        <Loader2 aria-hidden className="size-4 animate-spin text-(--ui-text-tertiary)" />
      </ClarifyShell>
    )
  }

  const onDraftChange = (value: string) => {
    setDraft(value)

    // Typing is its own answer — drop any picked choice so the two inputs can't
    // both look selected.
    if (value.trim()) {
      setSelectedChoices([])
    }
  }

  return (
    <ClarifyShell className="grid gap-2 px-2.5 py-2" dir={direction}>
      <div className="flex items-start gap-2">
        <span
          className="flex-1 whitespace-pre-wrap font-medium leading-(--conversation-line-height)"
          data-bidi-plaintext=""
          dir="auto"
        >
          {question}
        </span>
        <MessageQuestion aria-hidden className="mt-px size-4 shrink-0 text-(--ui-text-tertiary)" />
      </div>

      <form className="grid gap-2" onSubmit={handleSubmit}>
        {hasChoices ? (
          <div className="grid gap-px" role="group">
            {choices.map((choice, index) => (
              <button
                aria-pressed={selectedChoices.includes(choice)}
                className={cn(
                  OPTION_ROW_CLASS,
                  'text-(--ui-text-secondary) hover:bg-(--chrome-action-hover) hover:text-(--ui-text-primary)',
                  selectedChoices.includes(choice) && 'text-(--ui-text-primary)'
                )}
                data-choice
                disabled={submitting}
                key={`${index}-${choice}`}
                onClick={() => selectChoice(choice)}
                type="button"
              >
                <KeyBadge char={letterFor(index)} selected={selectedChoices.includes(choice)} />
                <span className="flex-1 wrap-anywhere" data-bidi-plaintext="" dir="auto">
                  {choice}
                </span>
              </button>
            ))}
            <label className={cn(OPTION_ROW_CLASS, 'items-center')}>
              <KeyBadge char={letterFor(choices.length)} preview={otherFocused} selected={Boolean(trimmedDraft)} />
              <Textarea
                className={CLARIFY_TEXTAREA_CLASS}
                dir={direction}
                disabled={submitting}
                onBlur={() => setOtherFocused(false)}
                onChange={event => onDraftChange(event.target.value)}
                onFocus={() => {
                  setSelectedChoices([])
                  setOtherFocused(true)
                }}
                onKeyDown={handleTextareaKey}
                placeholder={actionCopy.other}
                ref={textareaRef}
                rows={1}
                size="sm"
                value={draft}
              />
            </label>
          </div>
        ) : (
          <Textarea
            className={CLARIFY_TEXTAREA_CLASS}
            dir={direction}
            disabled={submitting}
            onChange={event => onDraftChange(event.target.value)}
            onKeyDown={handleTextareaKey}
            placeholder={actionCopy.placeholder}
            ref={textareaRef}
            rows={1}
            size="sm"
            value={draft}
          />
        )}

        <div className={cn('flex items-center gap-1', rtl ? 'justify-start' : 'justify-end')}>
          <Button
            disabled={submitting}
            onClick={() => void respond('', { allowEmpty: true })}
            size="xs"
            type="button"
            variant="text"
          >
            {actionCopy.skip}
          </Button>
          <Button disabled={submitting || !pendingAnswer} size="xs" type="submit">
            {submitting ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <>
                {actionCopy.continueLabel}
                <span aria-hidden className={cn('text-[0.625rem] opacity-70', rtl ? 'mr-0.5' : 'ml-0.5')}>
                  ⏎
                </span>
              </>
            )}
          </Button>
        </div>
      </form>
    </ClarifyShell>
  )
}
