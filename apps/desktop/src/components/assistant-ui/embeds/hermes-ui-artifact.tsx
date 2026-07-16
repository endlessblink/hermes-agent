'use client'

import { type CSSProperties, useEffect, useMemo, useState } from 'react'

import { requestComposerSubmit } from '@/app/chat/composer/focus'
import { useI18n } from '@/i18n'
import {
  HERMES_UI_TASK_BREAKDOWN_LIMITS,
  type HermesUiArtifact,
  type HermesUiMutationPreviewArtifact,
  type HermesUiTaskBreakdownArtifact,
  type HermesUiTaskBreakdownStep,
  parseHermesUiArtifact,
  parseHermesUiTaskBreakdownDraftSteps,
  stableArtifactStorageKey
} from '@/lib/hermes-ui-artifacts'
import { readKey, writeKey } from '@/lib/storage'
import { cn } from '@/lib/utils'

import type { RichFenceProps } from './types'

function hasRtlText(value: string | undefined): boolean {
  return /[\u0590-\u05ff\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]/.test(value || '')
}

function artifactDirection(artifact: HermesUiArtifact): 'ltr' | 'rtl' {
  if (artifact.direction === 'ltr' || artifact.direction === 'rtl') {
    return artifact.direction
  }

  const text = artifact.type === 'task-breakdown'
    ? [artifact.title, artifact.description, artifact.task.title, ...artifact.steps.flatMap(step => [step.title, step.doneEnough])]
    : [artifact.title, artifact.description, ...artifact.changes.map(change => change.title)]

  return text.some(hasRtlText) ? 'rtl' : 'ltr'
}

function useArtifactLayout(artifact: HermesUiArtifact) {
  const direction = artifactDirection(artifact)
  const isRtl = direction === 'rtl'
  const style = { direction, textAlign: isRtl ? 'right' : 'left' } satisfies CSSProperties

  return { direction, isRtl, style }
}

function ArtifactShell({ artifact, children }: { artifact: HermesUiArtifact; children: React.ReactNode }) {
  const { t } = useI18n()
  const { direction, isRtl, style } = useArtifactLayout(artifact)

  return (
    <section
      aria-label={artifact.title || (artifact.type === 'task-breakdown' ? artifact.task.title : t.assistantUi.previewLabel)}
      className={cn('my-3 overflow-hidden rounded-md border border-border/80 bg-muted/25', isRtl ? 'text-right' : 'text-left')}
      data-hermes-ui-artifact={artifact.type}
      dir={direction}
      style={style}
    >
      {(artifact.title || artifact.description) && (
        <header className="border-b border-border/65 px-3 py-2.5">
          {artifact.title && <h3 className="m-0 text-[0.8125rem] leading-snug font-semibold text-foreground">{artifact.title}</h3>}
          {artifact.description && <p className="m-0 mt-1 text-[0.72rem] leading-relaxed text-muted-foreground">{artifact.description}</p>}
        </header>
      )}
      {children}
    </section>
  )
}

function readBreakdownDraft(key: string, artifact: HermesUiTaskBreakdownArtifact): HermesUiTaskBreakdownStep[] {
  const fallback = () => artifact.steps.map(step => ({ ...step }))
  const raw = readKey(key)

  if (!raw) {
    return fallback()
  }

  try {
    return parseHermesUiTaskBreakdownDraftSteps(JSON.parse(raw)) || fallback()
  } catch {
    return fallback()
  }
}

function stepIdentity(step: HermesUiTaskBreakdownStep): string {
  if (step.subtaskId) {
    return `subtaskId:${step.subtaskId}`
  }

  if (step.clientId) {
    return `clientId:${step.clientId}`
  }

  throw new Error('Task breakdown step is missing a stable identity')
}

function nextClientId(artifact: HermesUiTaskBreakdownArtifact, steps: HermesUiTaskBreakdownStep[]): string {
  const used = new Set(steps.flatMap(step => (step.clientId ? [step.clientId] : [])))
  let index = 1

  while (true) {
    const suffix = `-new-${index}`
    const candidate = `${artifact.proposalId.slice(0, 160 - suffix.length)}${suffix}`

    if (!used.has(candidate)) {
      return candidate
    }

    index += 1
  }
}

export function TaskBreakdownCard({ artifact }: { artifact: HermesUiTaskBreakdownArtifact }) {
  const { t } = useI18n()
  const copy = t.assistantUi
  const { direction, isRtl, style } = useArtifactLayout(artifact)
  const storageKey = useMemo(() => stableArtifactStorageKey(artifact), [artifact])
  const [steps, setSteps] = useState(() => readBreakdownDraft(storageKey, artifact))
  const [submission, setSubmission] = useState<'failed' | 'idle' | 'sent' | 'sending'>('idle')

  useEffect(() => {
    setSteps(readBreakdownDraft(storageKey, artifact))
    setSubmission('idle')
    // Proposal revisions are immutable; reconstructed parent props must not reset edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey])

  const replaceSteps = (update: (current: HermesUiTaskBreakdownStep[]) => HermesUiTaskBreakdownStep[]) => {
    setSubmission('idle')
    setSteps(current => {
      const next = update(current)

      writeKey(storageKey, JSON.stringify(next))

      return next
    })
  }

  const updateStep = (identity: string, patch: Partial<HermesUiTaskBreakdownStep>) => {
    replaceSteps(current => current.map(step => (stepIdentity(step) === identity ? { ...step, ...patch } : step)))
  }

  const moveStep = (index: number, offset: -1 | 1) => {
    const target = index + offset

    if (target < 0 || target >= steps.length) {
      return
    }

    replaceSteps(current => {
      const next = [...current]
      const [step] = next.splice(index, 1)

      if (step) {
        next.splice(target, 0, step)
      }

      return next
    })
  }

  const valid = steps.every(step =>
    step.title.trim() &&
    step.doneEnough.trim() &&
    (step.estimateMinutes === undefined || (
      Number.isInteger(step.estimateMinutes) && step.estimateMinutes >= 1 && step.estimateMinutes <= 480
    ))
  )

  const submit = async () => {
    if (!valid || submission === 'sending') {
      return
    }

    const decision = {
      action: 'revise',
      approval: false,
      proposalId: artifact.proposalId,
      proposalRevision: artifact.proposalRevision,
      schemaVersion: artifact.schemaVersion,
      scope: artifact.scope,
      steps: steps.map(step => ({
        ...(step.subtaskId ? { subtaskId: step.subtaskId } : { clientId: step.clientId }),
        doneEnough: step.doneEnough.trim(),
        ...(step.estimateMinutes === undefined ? {} : { estimateMinutes: step.estimateMinutes }),
        optional: step.optional === true,
        title: step.title.trim()
      })),
      stoppingRule: artifact.stoppingRule,
      targetOutcome: artifact.targetOutcome,
      task: artifact.task,
      type: 'task-breakdown-revision'
    }

    setSubmission('sending')

    const accepted = await requestComposerSubmit(
      `Hermes UI task breakdown revision (not approval):\n${JSON.stringify(decision)}`,
      { flowstateDecision: decision, hidden: true, target: 'main' }
    )

    setSubmission(accepted ? 'sent' : 'failed')
  }

  return (
    <ArtifactShell artifact={artifact}>
      <div className="flex items-center justify-between gap-3 border-b border-border/55 px-3 py-2" dir={direction} style={style}>
        <span className="min-w-0 truncate text-[0.75rem] font-medium text-foreground">{artifact.task.title}</span>
        <span className="shrink-0 rounded-md border border-border/70 px-2 py-1 text-[0.68rem] text-muted-foreground">
          {copy.scope[artifact.scope]}
        </span>
      </div>

      {(artifact.targetOutcome || artifact.stoppingRule) && (
        <div className="grid gap-2 border-b border-border/55 px-3 py-2.5 sm:grid-cols-2">
          {artifact.targetOutcome && (
            <div className="rounded-md border border-border/55 bg-background/35 px-2.5 py-2">
              <div className="text-[0.66rem] font-medium text-muted-foreground">{copy.targetOutcome}</div>
              <div className="mt-1 text-[0.74rem] leading-relaxed text-foreground">{artifact.targetOutcome}</div>
            </div>
          )}
          {artifact.stoppingRule && (
            <div className="rounded-md border border-border/55 bg-background/35 px-2.5 py-2">
              <div className="text-[0.66rem] font-medium text-muted-foreground">{copy.stoppingRule}</div>
              <div className="mt-1 text-[0.74rem] leading-relaxed text-foreground">{artifact.stoppingRule}</div>
            </div>
          )}
        </div>
      )}

      <div className="space-y-2 px-3 py-3">
        {steps.map((step, index) => {
          const identity = stepIdentity(step)

          return (
            <div className="rounded-lg border border-border/60 bg-background/35 p-2.5" key={identity}>
              <div className={cn('mb-1.5 flex items-center justify-between gap-2', isRtl && 'flex-row-reverse')}>
                <span className="text-[0.68rem] font-medium text-muted-foreground">{copy.step} {index + 1}</span>
                <div className="flex items-center gap-1">
                  <button aria-label={`${copy.moveUp} ${step.title}`} disabled={index === 0} onClick={() => moveStep(index, -1)} type="button">↑</button>
                  <button aria-label={`${copy.moveDown} ${step.title}`} disabled={index === steps.length - 1} onClick={() => moveStep(index, 1)} type="button">↓</button>
                  <button
                    aria-label={`${copy.remove} ${step.title}`}
                    disabled={steps.length === 1}
                    onClick={() => replaceSteps(current => current.filter(item => stepIdentity(item) !== identity))}
                    type="button"
                  >×</button>
                </div>
              </div>
              <input
                aria-label={`${copy.step} ${index + 1}`}
                className="w-full rounded-md border border-border/70 bg-background px-2 py-1.5 text-[0.76rem] text-foreground"
                maxLength={HERMES_UI_TASK_BREAKDOWN_LIMITS.titleLength}
                onChange={event => updateStep(identity, { title: event.currentTarget.value })}
                value={step.title}
              />
              <input
                aria-label={`${copy.doneEnough} ${index + 1}`}
                className="mt-1.5 w-full rounded-md border border-border/70 bg-background px-2 py-1.5 text-[0.72rem] text-foreground"
                maxLength={HERMES_UI_TASK_BREAKDOWN_LIMITS.doneEnoughLength}
                onChange={event => updateStep(identity, { doneEnough: event.currentTarget.value })}
                placeholder={copy.doneEnough}
                value={step.doneEnough}
              />
              <div className={cn('mt-1.5 flex flex-wrap items-center gap-3', isRtl && 'justify-end')}>
                <label className="text-[0.68rem] text-muted-foreground">
                  {copy.estimateMinutes}
                  <input
                    aria-label={`${copy.estimateMinutes} ${index + 1}`}
                    className="mx-1 w-20 rounded-md border border-border/70 bg-background px-2 py-1 text-[0.72rem] text-foreground"
                    max={480}
                    min={1}
                    onChange={event => updateStep(identity, {
                      estimateMinutes: event.currentTarget.value === '' ? undefined : event.currentTarget.valueAsNumber
                    })}
                    type="number"
                    value={step.estimateMinutes ?? ''}
                  />
                </label>
                <label className="inline-flex items-center gap-1.5 text-[0.68rem] text-muted-foreground">
                  <input
                    aria-label={`${copy.optional} ${index + 1}`}
                    checked={step.optional === true}
                    onChange={event => updateStep(identity, { optional: event.currentTarget.checked })}
                    type="checkbox"
                  />
                  {copy.optional}
                </label>
              </div>
            </div>
          )
        })}
      </div>

      <footer className={cn('flex flex-wrap items-center gap-2 border-t border-border/65 px-3 py-2.5', isRtl && 'justify-end')}>
        <button
          className="rounded-md border border-border/80 bg-background/45 px-2 py-1 text-[0.72rem]"
          disabled={steps.length >= HERMES_UI_TASK_BREAKDOWN_LIMITS.stepCount}
          onClick={() => replaceSteps(current => [...current, { clientId: nextClientId(artifact, current), doneEnough: '', title: '' }])}
          type="button"
        >
          {copy.addStep}
        </button>
        {submission === 'failed' && <span className="text-[0.68rem] font-medium text-destructive">{copy.sendFailed}</span>}
        <button
          className="rounded-md bg-foreground px-2.5 py-1 text-[0.72rem] font-medium text-background disabled:opacity-45"
          disabled={!valid || submission === 'sending'}
          onClick={() => void submit()}
          type="button"
        >
          {submission === 'sending' ? copy.sending : submission === 'sent' ? copy.sent : artifact.submitLabel || copy.updateBreakdown}
        </button>
      </footer>
    </ArtifactShell>
  )
}

function displayExactOperationValue(
  value: boolean | null | number | string | { x: number; y: number } | undefined,
  copy: ReturnType<typeof useI18n>['t']['assistantUi']
): string {
  if (value === undefined || value === null || value === '') {
    return copy.unknown
  }

  if (typeof value === 'boolean') {
    return value ? copy.yes : copy.no
  }

  if (typeof value === 'object') {
    return `x: ${value.x}, y: ${value.y}`
  }

  return String(value)
}

export function MutationPreviewCard({ artifact }: { artifact: HermesUiMutationPreviewArtifact }) {
  const { t } = useI18n()
  const copy = t.assistantUi
  const { direction, isRtl, style } = useArtifactLayout(artifact)
  const [correction, setCorrection] = useState('')
  const [submission, setSubmission] = useState<'approve' | 'failed' | 'idle' | 'revise' | 'sending-approve' | 'sending-revise'>('idle')
  const approvalIdentity = useMemo(() => JSON.stringify(artifact.canonicalApproval), [artifact.canonicalApproval])
  const expiry = Date.parse(artifact.canonicalApproval.previewExpiresAt)
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    setCorrection('')
    setSubmission('idle')
    setNow(Date.now())
  }, [approvalIdentity])

  useEffect(() => {
    if (expiry <= now) {
      return
    }

    const timeout = window.setTimeout(() => setNow(Date.now()), Math.min(expiry - now + 10, 2_147_483_647))

    return () => window.clearTimeout(timeout)
  }, [expiry, now])

  const expired = expiry <= now

  const safeArtifact = useMemo(
    () => ({ ...artifact, description: undefined, title: copy.previewLabel }),
    [artifact, copy.previewLabel]
  )

  const submit = async (decision: 'approve' | 'revise') => {
    if (submission !== 'idle' && submission !== 'failed') {
      return
    }

    if (decision === 'approve' && (expired || correction.trim())) {
      return
    }

    const payload = {
      ...artifact.canonicalApproval,
      approval: decision === 'approve',
      ...(decision === 'revise' ? { correction: correction.trim() } : {}),
      decision,
      schemaVersion: 1,
      type: 'flowstate-mutation-decision'
    }

    setSubmission(decision === 'approve' ? 'sending-approve' : 'sending-revise')

    const accepted = await requestComposerSubmit(
      `Hermes UI canonical mutation decision:\n${JSON.stringify(payload)}`,
      { flowstateDecision: payload, hidden: true, target: 'main' }
    )

    setSubmission(accepted ? decision : 'failed')
  }

  return (
    <ArtifactShell artifact={safeArtifact}>
      <div className="space-y-2 px-3 py-3" dir={direction} style={style}>
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2.5 py-2 text-[0.72rem] font-medium text-amber-800 dark:text-amber-200">
          {copy.canonicalPreview}
        </div>

        <div className="space-y-2 rounded-md border border-border/60 bg-background/30 p-2.5">
          <div className="text-[0.68rem] text-muted-foreground">
            {copy.exactOperations}: {artifact.canonicalApproval.operations.length} · {copy.baseRevision}: {artifact.canonicalApproval.baseRevision}
          </div>
          <div className="space-y-1">
            {artifact.canonicalApproval.operations.map((operation, index) => {
              const identity = 'clientId' in operation ? operation.clientId : operation.subtaskId
              const fields = Object.entries(operation).filter(([key]) => key !== 'kind')

              return (
                <div
                  className="rounded-md border border-border/55 bg-background/45 px-2.5 py-2 text-[0.68rem] text-foreground"
                  data-exact-operation={operation.kind}
                  key={`${operation.kind}:${identity}`}
                >
                  <div className="font-semibold">
                    {index + 1}. {copy.operationKinds[operation.kind]}
                  </div>
                  <dl className="mt-1 grid gap-x-3 gap-y-1 sm:grid-cols-[minmax(7rem,auto)_1fr]">
                    {fields.map(([key, value]) => (
                      <div className="contents" key={key}>
                        <dt className="text-muted-foreground">{copy.mutationFields[key as keyof typeof copy.mutationFields]}</dt>
                        <dd className="m-0 whitespace-pre-wrap wrap-anywhere">
                          {displayExactOperationValue(value, copy)}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </div>
              )
            })}
          </div>
          <label className="block text-[0.7rem] font-medium text-foreground">
            {copy.correction}
            <textarea
              aria-label={copy.correction}
              className="mt-1 min-h-20 w-full resize-y rounded-md border border-border/70 bg-background px-2 py-1.5 text-[0.72rem] text-foreground"
              disabled={submission === 'approve' || submission === 'revise'}
              maxLength={1000}
              onChange={event => setCorrection(event.currentTarget.value)}
              placeholder={copy.correctionPlaceholder}
              value={correction}
            />
          </label>
          {expired && <div className="text-[0.68rem] font-medium text-amber-700 dark:text-amber-300">{copy.expired}</div>}
          {submission === 'failed' && <div className="text-[0.68rem] font-medium text-destructive">{copy.sendFailed}</div>}
          <div className={cn('flex flex-wrap gap-1.5', isRtl && 'justify-end')}>
            <button
              className="rounded-md border border-border/80 bg-background/45 px-2 py-1 text-[0.7rem] font-medium text-foreground disabled:opacity-50"
              disabled={submission !== 'idle' && submission !== 'failed'}
              onClick={() => void submit('revise')}
              type="button"
            >
              {submission === 'sending-revise' ? copy.sending : submission === 'revise' ? copy.sent : copy.requestPreview}
            </button>
            <button
              className="rounded-md border border-emerald-600/50 bg-emerald-600/10 px-2 py-1 text-[0.7rem] font-semibold text-emerald-800 disabled:opacity-50 dark:text-emerald-200"
              disabled={expired || Boolean(correction.trim()) || (submission !== 'idle' && submission !== 'failed')}
              onClick={() => void submit('approve')}
              type="button"
            >
              {submission === 'sending-approve' ? copy.sending : submission === 'approve' ? copy.approvedAndSent : copy.approveExact}
            </button>
          </div>
        </div>
      </div>
    </ArtifactShell>
  )
}

export default function HermesUiArtifactRenderer({ code }: RichFenceProps) {
  const result = parseHermesUiArtifact(code)

  if (!result.ok) {
    return null
  }

  return result.artifact.type === 'task-breakdown'
    ? <TaskBreakdownCard artifact={result.artifact} />
    : <MutationPreviewCard artifact={result.artifact} />
}
