'use client'

import { type CSSProperties, useId, useMemo, useState } from 'react'

import { requestComposerSubmit } from '@/app/chat/composer/focus'
import {
  type HermesUiChecklistArtifact,
  type HermesUiFlowStateBatchArtifact,
  type HermesUiFlowStateNextBlockArtifact,
  type HermesUiQuestionnaireArtifact,
  type HermesUiTaskPriority,
  type HermesUiTaskTriageArtifact,
  type HermesUiTriageDecision,
  parseHermesUiArtifact,
  stableArtifactStorageKey
} from '@/lib/hermes-ui-artifacts'
import { readKey, writeKey } from '@/lib/storage'
import { cn } from '@/lib/utils'

import type { RichFenceProps } from './types'

function readChecklistState(key: string, itemIds: ReadonlySet<string>): Record<string, boolean> {
  const raw = readKey(key)

  if (!raw) {
    return {}
  }

  try {
    const parsed = JSON.parse(raw)

    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return {}
    }

    return Object.fromEntries(
      Object.entries(parsed)
        .filter((entry): entry is [string, boolean] => itemIds.has(entry[0]) && typeof entry[1] === 'boolean')
        .map(([id, checked]) => [id, checked])
    )
  } catch {
    return {}
  }
}

function persistChecklistState(key: string, itemIds: readonly string[], state: Record<string, boolean>) {
  writeKey(
    key,
    JSON.stringify(Object.fromEntries(itemIds.map(id => [id, Boolean(state[id])] as const)))
  )
}

function hasRtlText(value: string | undefined): boolean {
  return /[\u0590-\u05ff\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]/.test(value || '')
}

function artifactDirection(
  artifact:
    | HermesUiChecklistArtifact
    | HermesUiFlowStateBatchArtifact
    | HermesUiFlowStateNextBlockArtifact
    | HermesUiQuestionnaireArtifact
    | HermesUiTaskTriageArtifact
): 'ltr' | 'rtl' {
  if (artifact.direction === 'rtl' || artifact.direction === 'ltr') {
    return artifact.direction
  }

  const taskText = artifact.type === 'task-triage' ? [artifact.task.title, artifact.task.status] : []
  const batchText = artifact.type === 'flowstate-task-batch' ? artifact.tasks.flatMap(task => [task.title, task.status, task.rationale]) : []

  const nextBlockText =
    artifact.type === 'flowstate-next-block' ? [artifact.task.title, artifact.doneEnough, artifact.rationale] : []

  const itemText =
    artifact.type === 'checklist' || artifact.type === 'questionnaire'
      ? artifact.items.flatMap(item => [item.label, item.description])
      : []

  const sample = [artifact.title, artifact.description, ...taskText, ...batchText, ...nextBlockText, ...itemText]
    .filter(Boolean)
    .join('\n')

  return hasRtlText(sample) ? 'rtl' : 'ltr'
}

function splitTextBlocks(value: string): string[] {
  return value
    .split(/\n{2,}/)
    .map(part => part.trim())
    .filter(Boolean)
}

function PlainTextBlocks({ className, text }: { className?: string; text: string }) {
  const blocks = splitTextBlocks(text)

  if (blocks.length <= 1) {
    return (
      <span className={className} dir="auto">
        {text}
      </span>
    )
  }

  return (
    <span className={cn('block space-y-1.5', className)} dir="auto">
      {blocks.map((block, index) => (
        <span className="block" key={`${index}:${block.slice(0, 16)}`}>
          {block}
        </span>
      ))}
    </span>
  )
}

export function ChecklistArtifactCard({ artifact }: { artifact: HermesUiChecklistArtifact | HermesUiQuestionnaireArtifact }) {
  const reactId = useId()
  const itemIds = useMemo(() => artifact.items.map(item => item.id), [artifact.items])
  const itemIdSet = useMemo(() => new Set(itemIds), [itemIds])
  const storageKey = useMemo(() => stableArtifactStorageKey(artifact), [artifact])
  const [checked, setChecked] = useState<Record<string, boolean>>(() => readChecklistState(storageKey, itemIdSet))
  const [handledActionId, setHandledActionId] = useState<string | null>(null)
  const checkedCount = itemIds.reduce((total, id) => total + (checked[id] ? 1 : 0), 0)
  const percent = itemIds.length === 0 ? 0 : (checkedCount / itemIds.length) * 100
  const direction = artifactDirection(artifact)
  const isRtl = direction === 'rtl'
  const directionalStyle = { direction, textAlign: isRtl ? 'right' : 'left' } satisfies CSSProperties

  const updateChecked = (next: Record<string, boolean>) => {
    setChecked(next)
    persistChecklistState(storageKey, itemIds, next)
  }

  const runAction = async (actionKey: string, action: NonNullable<HermesUiChecklistArtifact['items'][number]['actions']>[number]) => {
    if (action.submitText) {
      requestComposerSubmit(action.submitText, { target: 'main' })
      setHandledActionId(actionKey)

      return
    }

    if (action.copyText) {
      if (window.hermesDesktop?.writeClipboard) {
        await window.hermesDesktop.writeClipboard(action.copyText)
      } else {
        await navigator.clipboard.writeText(action.copyText)
      }

      setHandledActionId(actionKey)
    }
  }

  return (
    <section
      aria-label={artifact.title || 'Interactive checklist'}
      className={cn(
        'my-3 overflow-hidden rounded-xl border border-border/80 bg-muted/25 shadow-[0_0.0625rem_0.125rem_color-mix(in_srgb,#000_10%,transparent)]',
        isRtl ? 'text-right' : 'text-left'
      )}
      data-hermes-ui-artifact={artifact.type}
      dir={direction}
      style={directionalStyle}
    >
      <div className="border-b border-border/65 px-3 py-2.5">
        <div className={cn('flex min-w-0 items-start justify-between gap-3', isRtl && 'flex-row-reverse')}>
          <div className="min-w-0">
            {artifact.title && (
              <h3 className="m-0 text-[0.8125rem] leading-snug font-semibold text-foreground" dir={direction} style={directionalStyle}>
                {artifact.title}
              </h3>
            )}
            {artifact.description && (
              <p className="m-0 mt-1 text-[0.75rem] leading-relaxed text-muted-foreground" dir={direction} style={directionalStyle}>
                <PlainTextBlocks text={artifact.description} />
              </p>
            )}
          </div>
          <span className="shrink-0 rounded-md border border-border/70 bg-background/45 px-1.5 py-0.5 text-[0.6875rem] leading-none font-medium text-muted-foreground tabular-nums">
            {checkedCount} / {itemIds.length}
          </span>
        </div>
        <div
          aria-label={`${checkedCount} of ${itemIds.length} checklist items complete`}
          aria-valuemax={itemIds.length}
          aria-valuemin={0}
          aria-valuenow={checkedCount}
          className="mt-2 h-1.5 overflow-hidden rounded-full bg-background/70"
          role="progressbar"
        >
          <div className="h-full rounded-full bg-foreground/70 transition-[width]" style={{ width: `${percent}%` }} />
        </div>
      </div>
      <div className="divide-y divide-border/45">
        {artifact.items.map(item => {
          const inputId = `${reactId}-${item.id}`

          return (
            <div
              className={cn('flex gap-2.5 px-3 py-2.5', isRtl && 'flex-row-reverse')}
              dir={direction}
              key={item.id}
              style={directionalStyle}
            >
              <input
                checked={Boolean(checked[item.id])}
                className="mt-0.5 size-4 shrink-0 accent-foreground"
                id={inputId}
                onChange={event => updateChecked({ ...checked, [item.id]: event.currentTarget.checked })}
                type="checkbox"
              />
              <div className="min-w-0 flex-1">
                <label
                  className={cn(
                    'block cursor-pointer whitespace-pre-wrap text-[0.8125rem] leading-relaxed text-foreground wrap-anywhere',
                    checked[item.id] && 'text-muted-foreground line-through decoration-muted-foreground/50'
                  )}
                  dir={direction}
                  htmlFor={inputId}
                  style={directionalStyle}
                >
                  <PlainTextBlocks text={item.label} />
                </label>
                {item.description && (
                  <p
                    className="m-0 mt-1 text-[0.75rem] leading-relaxed text-muted-foreground wrap-anywhere"
                    dir={direction}
                    style={directionalStyle}
                  >
                    <PlainTextBlocks text={item.description} />
                  </p>
                )}
                {item.actions?.length ? (
                  <div className={cn('mt-2 flex flex-wrap gap-1.5', isRtl && 'justify-end')}>
                    {item.actions.map(action => {
                      const actionKey = `${item.id}:${action.id}`

                      return (
                        <button
                          className="rounded-md border border-border/70 bg-background/45 px-2 py-1 text-[0.6875rem] font-medium text-muted-foreground hover:bg-muted/70 hover:text-foreground"
                          key={action.id}
                          onClick={() => void runAction(actionKey, action)}
                          title={handledActionId === actionKey ? (action.submitText ? (isRtl ? 'נשלח' : 'Sent') : isRtl ? 'הועתק' : 'Copied') : undefined}
                          type="button"
                        >
                          {handledActionId === actionKey ? (action.submitText ? (isRtl ? 'נשלח' : 'Sent') : isRtl ? 'הועתק' : 'Copied') : action.label}
                        </button>
                      )
                    })}
                  </div>
                ) : null}
              </div>
            </div>
          )
        })}
      </div>
      <div className={cn('flex items-center gap-2 border-t border-border/65 px-3 py-2', isRtl && 'justify-end')}>
        <button
          className="rounded-md border border-border/80 bg-background/45 px-2 py-1 text-[0.75rem] font-medium text-foreground hover:bg-muted/70"
          onClick={() => updateChecked(Object.fromEntries(itemIds.map(id => [id, true] as const)))}
          type="button"
        >
          {isRtl ? 'סמן הכול' : 'Mark all'}
        </button>
        <button
          className="rounded-md border border-border/80 bg-transparent px-2 py-1 text-[0.75rem] font-medium text-muted-foreground hover:bg-muted/60 hover:text-foreground"
          onClick={() => updateChecked(Object.fromEntries(itemIds.map(id => [id, false] as const)))}
          type="button"
        >
          {isRtl ? 'נקה' : 'Clear'}
        </button>
      </div>
    </section>
  )
}


const PRIORITY_LABELS: Record<Exclude<HermesUiTaskPriority, null>, { ltr: string; rtl: string }> = {
  high: { ltr: 'High', rtl: 'גבוהה' },
  low: { ltr: 'Low', rtl: 'נמוכה' },
  medium: { ltr: 'Medium', rtl: 'בינונית' }
}

function formatPriority(priority: HermesUiTaskPriority | undefined, isRtl: boolean): string {
  if (!priority) {
    return isRtl ? 'ללא' : 'None'
  }

  return PRIORITY_LABELS[priority][isRtl ? 'rtl' : 'ltr']
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10)
}

function plusDaysIso(days: number): string {
  const date = new Date()
  date.setDate(date.getDate() + days)

  return date.toISOString().slice(0, 10)
}

function taskTriageCopyText(
  artifact: HermesUiTaskTriageArtifact,
  decision: string,
  dueDate: string,
  priority: HermesUiTaskPriority,
  isRtl: boolean
): string {
  const label = isRtl ? 'החלטת triage למשימת FlowState' : 'FlowState task triage decision'
  const currentDue = artifact.task.dueDate || (isRtl ? 'אין' : 'none')
  const nextDue = dueDate || (isRtl ? 'ללא תאריך' : 'no due date')

  return [
    label,
    `ID: ${artifact.task.id}`,
    `${isRtl ? 'כותרת' : 'Title'}: ${artifact.task.title}`,
    `${isRtl ? 'החלטה' : 'Decision'}: ${decision || (isRtl ? 'לא נבחרה' : 'not selected')}`,
    `${isRtl ? 'תאריך נוכחי' : 'Current due date'}: ${currentDue}`,
    `${isRtl ? 'תאריך חדש' : 'New due date'}: ${nextDue}`,
    `${isRtl ? 'דחיפות חדשה' : 'New priority'}: ${formatPriority(priority, isRtl)}`,
    isRtl ? 'נא להציג לי preview לפני שינוי אמיתי ב־FlowState.' : 'Show me a preview before applying a real FlowState change.'
  ].join('\n')
}

export function TaskTriageArtifactCard({ artifact }: { artifact: HermesUiTaskTriageArtifact }) {
  const [decision, setDecision] = useState('')
  const [dueDate, setDueDate] = useState(artifact.task.dueDate || '')
  const [priority, setPriority] = useState<HermesUiTaskPriority>(artifact.task.priority ?? null)
  const [copied, setCopied] = useState(false)
  const direction = artifactDirection(artifact)
  const isRtl = direction === 'rtl'
  const directionalStyle = { direction, textAlign: isRtl ? 'right' : 'left' } satisfies CSSProperties

  const copyRequest = async () => {
    await navigator.clipboard.writeText(taskTriageCopyText(artifact, decision, dueDate, priority, isRtl))
    setCopied(true)
  }

  return (
    <section
      aria-label={artifact.title || artifact.task.title}
      className={cn(
        'my-3 overflow-hidden rounded-xl border border-border/80 bg-muted/25 shadow-[0_0.0625rem_0.125rem_color-mix(in_srgb,#000_10%,transparent)]',
        isRtl ? 'text-right' : 'text-left'
      )}
      data-hermes-ui-artifact="task-triage"
      dir={direction}
      style={directionalStyle}
    >
      <div className="border-b border-border/65 px-3 py-2.5">
        {artifact.title && (
          <h3 className="m-0 text-[0.8125rem] leading-snug font-semibold text-foreground" dir={direction} style={directionalStyle}>
            {artifact.title}
          </h3>
        )}
        {artifact.description && (
          <p className="m-0 mt-1 text-[0.75rem] leading-relaxed text-muted-foreground" dir={direction} style={directionalStyle}>
            <PlainTextBlocks text={artifact.description} />
          </p>
        )}
      </div>

      <div className="space-y-3 px-3 py-3" dir={direction} style={directionalStyle}>
        <div>
          <div className="text-[0.8125rem] leading-relaxed font-semibold text-foreground wrap-anywhere" dir={direction} style={directionalStyle}>
            {artifact.task.title}
          </div>
          <div className="mt-1 text-[0.75rem] leading-relaxed text-muted-foreground" dir={direction} style={directionalStyle}>
            {isRtl ? 'תאריך יעד' : 'Due'}: {artifact.task.dueDate || (isRtl ? 'אין' : 'none')} · {isRtl ? 'דחיפות' : 'Priority'}:{' '}
            {formatPriority(artifact.task.priority, isRtl)}
          </div>
        </div>

        <div className={cn('flex flex-wrap gap-1.5', isRtl && 'justify-end')}>
          {[
            [isRtl ? 'רלוונטית להיום' : 'Relevant today', 'relevant-today'],
            [isRtl ? 'לא להיום' : 'Not today', 'not-today'],
            [isRtl ? 'צריך דיון' : 'Discuss', 'discuss']
          ].map(([label, value]) => (
            <button
              className={cn(
                'rounded-md border px-2 py-1 text-[0.75rem] font-medium',
                decision === value
                  ? 'border-foreground bg-foreground text-background'
                  : 'border-border/80 bg-background/45 text-muted-foreground hover:bg-muted/70 hover:text-foreground'
              )}
              key={value}
              onClick={() => setDecision(value)}
              type="button"
            >
              {label}
            </button>
          ))}
        </div>

        <div className="grid gap-2 sm:grid-cols-2">
          <label className="block text-[0.75rem] font-medium text-muted-foreground" dir={direction} style={directionalStyle}>
            {isRtl ? 'שנה תאריך' : 'Change date'}
            <input
              className="mt-1 w-full rounded-md border border-border/80 bg-background/60 px-2 py-1.5 text-[0.8125rem] text-foreground"
              onChange={event => setDueDate(event.currentTarget.value)}
              type="date"
              value={dueDate}
            />
          </label>
          <label className="block text-[0.75rem] font-medium text-muted-foreground" dir={direction} style={directionalStyle}>
            {isRtl ? 'שנה דחיפות' : 'Change priority'}
            <select
              className="mt-1 w-full rounded-md border border-border/80 bg-background/60 px-2 py-1.5 text-[0.8125rem] text-foreground"
              onChange={event => setPriority((event.currentTarget.value || null) as HermesUiTaskPriority)}
              value={priority || ''}
            >
              <option value="">{isRtl ? 'ללא' : 'None'}</option>
              <option value="high">{isRtl ? 'גבוהה' : 'High'}</option>
              <option value="medium">{isRtl ? 'בינונית' : 'Medium'}</option>
              <option value="low">{isRtl ? 'נמוכה' : 'Low'}</option>
            </select>
          </label>
        </div>

        <div className={cn('flex flex-wrap gap-1.5', isRtl && 'justify-end')}>
          <button className="rounded-md border border-border/80 bg-background/45 px-2 py-1 text-[0.75rem] font-medium text-muted-foreground hover:bg-muted/70 hover:text-foreground" onClick={() => setDueDate(todayIso())} type="button">
            {isRtl ? 'היום' : 'Today'}
          </button>
          <button className="rounded-md border border-border/80 bg-background/45 px-2 py-1 text-[0.75rem] font-medium text-muted-foreground hover:bg-muted/70 hover:text-foreground" onClick={() => setDueDate(plusDaysIso(1))} type="button">
            {isRtl ? 'מחר' : 'Tomorrow'}
          </button>
          <button className="rounded-md border border-border/80 bg-background/45 px-2 py-1 text-[0.75rem] font-medium text-muted-foreground hover:bg-muted/70 hover:text-foreground" onClick={() => setDueDate(plusDaysIso(7))} type="button">
            {isRtl ? 'שבוע הבא' : 'Next week'}
          </button>
          <button className="rounded-md border border-border/80 bg-transparent px-2 py-1 text-[0.75rem] font-medium text-muted-foreground hover:bg-muted/60 hover:text-foreground" onClick={() => setDueDate('')} type="button">
            {isRtl ? 'ללא תאריך' : 'No date'}
          </button>
        </div>
      </div>

      <div className={cn('flex items-center gap-2 border-t border-border/65 px-3 py-2', isRtl && 'justify-end')}>
        <button
          className="rounded-md border border-border/80 bg-background/45 px-2 py-1 text-[0.75rem] font-medium text-foreground hover:bg-muted/70"
          onClick={() => void copyRequest()}
          type="button"
        >
          {copied ? (isRtl ? 'הועתק' : 'Copied') : isRtl ? 'העתק בקשה ליישום' : 'Copy apply request'}
        </button>
      </div>
    </section>
  )
}


function formatDecision(decision: HermesUiTriageDecision | '', isRtl: boolean): string {
  if (decision === 'today') {
    return isRtl ? 'היום' : 'Today'
  }

  if (decision === 'not_today') {
    return isRtl ? 'לא היום' : 'Not today'
  }

  if (decision === 'later') {
    return isRtl ? 'לדחות' : 'Defer'
  }

  if (decision === 'discuss') {
    return isRtl ? 'צריך דיון' : 'Discuss'
  }

  return isRtl ? 'לא נבחר' : 'not selected'
}

interface BatchDecisionState {
  completed: boolean
  decision: HermesUiTriageDecision | ''
  dueDate: string
  priority: HermesUiTaskPriority
}

function buildInitialBatchState(artifact: HermesUiFlowStateBatchArtifact): Record<string, BatchDecisionState> {
  return Object.fromEntries(
    artifact.tasks.map(task => [
      task.id,
      {
        completed: task.status === 'done',
        decision: task.recommendation || '',
        dueDate: task.recommendedDueDate ?? task.dueDate ?? '',
        priority: task.recommendedPriority ?? task.priority ?? null
      }
    ])
  )
}

function batchSubmitText(
  artifact: HermesUiFlowStateBatchArtifact,
  state: Record<string, BatchDecisionState>,
  isRtl: boolean
): string {
  const lines = [
    isRtl ? 'החלטות batch triage למשימות FlowState' : 'FlowState batch triage decisions',
    isRtl ? 'נא להציג preview לפני שינוי אמיתי ב־FlowState.' : 'Show a preview before applying real FlowState changes.',
    ''
  ]

  artifact.tasks.forEach((task, index) => {
    const decision = state[task.id] || { completed: task.status === 'done', decision: '', dueDate: task.dueDate || '', priority: task.priority ?? null }
    lines.push(`${index + 1}. ${task.title}`)
    lines.push(`ID: ${task.id}`)
    lines.push(`${isRtl ? 'החלטה' : 'Decision'}: ${formatDecision(decision.decision, isRtl)}`)
    lines.push(`${isRtl ? 'סימון ביצוע' : 'Completion'}: ${decision.completed ? (isRtl ? 'בוצע' : 'done') : (isRtl ? 'לא בוצע' : 'not done')}`)
    lines.push(`${isRtl ? 'תאריך נוכחי' : 'Current due date'}: ${task.dueDate || (isRtl ? 'אין' : 'none')}`)
    lines.push(`${isRtl ? 'תאריך מוצע' : 'Proposed due date'}: ${decision.dueDate || (isRtl ? 'ללא תאריך' : 'no date')}`)
    lines.push(`${isRtl ? 'דחיפות נוכחית' : 'Current priority'}: ${formatPriority(task.priority, isRtl)}`)
    lines.push(`${isRtl ? 'דחיפות מוצעת' : 'Proposed priority'}: ${formatPriority(decision.priority, isRtl)}`)

    if (task.rationale) {
      lines.push(`${isRtl ? 'נימוק assistant' : 'Assistant rationale'}: ${task.rationale}`)
    }

    lines.push('')
  })

  return lines.join('\n').trim()
}

export function FlowStateTaskBatchCard({ artifact }: { artifact: HermesUiFlowStateBatchArtifact }) {
  const direction = artifactDirection(artifact)
  const isRtl = direction === 'rtl'
  const directionalStyle = { direction, textAlign: isRtl ? 'right' : 'left' } satisfies CSSProperties
  const [state, setState] = useState<Record<string, BatchDecisionState>>(() => buildInitialBatchState(artifact))
  const [submitted, setSubmitted] = useState(false)

  const updateTask = (id: string, patch: Partial<BatchDecisionState>) => {
    setState(current => ({ ...current, [id]: { ...current[id], ...patch } as BatchDecisionState }))
    setSubmitted(false)
  }

  const submitBatch = () => {
    requestComposerSubmit(batchSubmitText(artifact, state, isRtl), { target: 'main' })
    setSubmitted(true)
  }

  return (
    <section
      aria-label={artifact.title || (isRtl ? 'FlowState batch triage' : 'FlowState batch triage')}
      className={cn(
        'my-3 overflow-hidden rounded-xl border border-border/80 bg-muted/25 shadow-[0_0.0625rem_0.125rem_color-mix(in_srgb,#000_10%,transparent)]',
        isRtl ? 'text-right' : 'text-left'
      )}
      data-hermes-ui-artifact="flowstate-task-batch"
      dir={direction}
      style={directionalStyle}
    >
      <div className="border-b border-border/65 px-3 py-2.5">
        <div className={cn('flex min-w-0 items-start justify-between gap-3', isRtl && 'flex-row-reverse')}>
          <div className="min-w-0">
            {artifact.title && (
              <h3 className="m-0 text-[0.8125rem] leading-snug font-semibold text-foreground" dir={direction} style={directionalStyle}>
                {artifact.title}
              </h3>
            )}
            {artifact.description && (
              <p className="m-0 mt-1 text-[0.75rem] leading-relaxed text-muted-foreground" dir={direction} style={directionalStyle}>
                <PlainTextBlocks text={artifact.description} />
              </p>
            )}
          </div>
          <span className="shrink-0 rounded-md border border-border/70 bg-background/45 px-1.5 py-0.5 text-[0.6875rem] leading-none font-medium text-muted-foreground tabular-nums">
            {artifact.tasks.length}
          </span>
        </div>
      </div>

      <div className="divide-y divide-border/45">
        {artifact.tasks.map(task => {
          const taskState = state[task.id] || { completed: task.status === 'done', decision: '', dueDate: task.dueDate || '', priority: task.priority ?? null }

          return (
            <div className="space-y-2 px-3 py-3" dir={direction} key={task.id} style={directionalStyle}>
              <div>
                <div className={cn('flex items-start gap-2', isRtl && 'flex-row-reverse')}>
                  <input
                    aria-label={`${task.title} — ${isRtl ? 'בוצע' : 'done'}`}
                    checked={taskState.completed}
                    className="mt-1 size-4 shrink-0 accent-foreground"
                    onChange={event => updateTask(task.id, { completed: event.currentTarget.checked })}
                    type="checkbox"
                  />
                  <div
                    className={cn(
                      'min-w-0 flex-1 text-[0.8125rem] leading-relaxed font-semibold text-foreground wrap-anywhere',
                      taskState.completed && 'text-muted-foreground line-through decoration-muted-foreground/50'
                    )}
                    dir={direction}
                    style={directionalStyle}
                  >
                    {task.title}
                  </div>
                </div>
                <div className="mt-1 text-[0.75rem] leading-relaxed text-muted-foreground" dir={direction} style={directionalStyle}>
                  {isRtl ? 'נוכחי' : 'Current'}: {task.dueDate || (isRtl ? 'אין תאריך' : 'no date')} · {formatPriority(task.priority, isRtl)}
                  <br />
                  {isRtl ? 'המלצה שלי' : 'My recommendation'}: {formatDecision(task.recommendation || '', isRtl)} · {formatPriority(task.recommendedPriority ?? task.priority, isRtl)} · {task.recommendedDueDate || task.dueDate || (isRtl ? 'ללא תאריך' : 'no date')}
                </div>
                {task.rationale && (
                  <div className="mt-1 text-[0.72rem] leading-relaxed text-muted-foreground/90" dir={direction} style={directionalStyle}>
                    {task.rationale}
                  </div>
                )}
              </div>

              <div className={cn('flex flex-wrap gap-1.5', isRtl && 'justify-end')}>
                {[
                  [isRtl ? 'היום' : 'Today', 'today'],
                  [isRtl ? 'לא היום' : 'Not today', 'not_today'],
                  [isRtl ? 'לדחות' : 'Defer', 'later'],
                  [isRtl ? 'דיון' : 'Discuss', 'discuss']
                ].map(([label, value]) => (
                  <button
                    className={cn(
                      'rounded-md border px-2 py-1 text-[0.72rem] font-medium',
                      taskState.decision === value
                        ? 'border-foreground bg-foreground text-background'
                        : 'border-border/80 bg-background/45 text-muted-foreground hover:bg-muted/70 hover:text-foreground'
                    )}
                    key={value}
                    onClick={() => updateTask(task.id, { decision: value as HermesUiTriageDecision })}
                    type="button"
                  >
                    {label}
                  </button>
                ))}
              </div>

              <div className="grid gap-2 sm:grid-cols-2">
                <label className="block text-[0.72rem] font-medium text-muted-foreground" dir={direction} style={directionalStyle}>
                  {isRtl ? 'תאריך מוצע' : 'Proposed date'}
                  <input
                    className="mt-1 w-full rounded-md border border-border/80 bg-background/60 px-2 py-1.5 text-[0.8125rem] text-foreground"
                    onChange={event => updateTask(task.id, { dueDate: event.currentTarget.value })}
                    type="date"
                    value={taskState.dueDate}
                  />
                </label>
                <label className="block text-[0.72rem] font-medium text-muted-foreground" dir={direction} style={directionalStyle}>
                  {isRtl ? 'דחיפות מוצעת' : 'Proposed priority'}
                  <select
                    className="mt-1 w-full rounded-md border border-border/80 bg-background/60 px-2 py-1.5 text-[0.8125rem] text-foreground"
                    onChange={event => updateTask(task.id, { priority: (event.currentTarget.value || null) as HermesUiTaskPriority })}
                    value={taskState.priority || ''}
                  >
                    <option value="">{isRtl ? 'ללא' : 'None'}</option>
                    <option value="high">{isRtl ? 'גבוהה' : 'High'}</option>
                    <option value="medium">{isRtl ? 'בינונית' : 'Medium'}</option>
                    <option value="low">{isRtl ? 'נמוכה' : 'Low'}</option>
                  </select>
                </label>
              </div>
            </div>
          )
        })}
      </div>

      <div className={cn('flex items-center gap-2 border-t border-border/65 px-3 py-2', isRtl && 'justify-end')}>
        <button
          className="rounded-md border border-border/80 bg-background/45 px-2 py-1 text-[0.75rem] font-medium text-foreground hover:bg-muted/70"
          onClick={submitBatch}
          type="button"
        >
          {submitted ? (isRtl ? 'נשלח ל־Hermes' : 'Sent to Hermes') : isRtl ? 'שלח החלטות ל־Hermes' : 'Send decisions to Hermes'}
        </button>
      </div>
    </section>
  )
}

function formatDurationMinutes(minutes: number, isRtl: boolean): string {
  if (isRtl) {
    return `${minutes} דקות`
  }

  return `${minutes} min`
}

export function FlowStateNextBlockCard({ artifact }: { artifact: HermesUiFlowStateNextBlockArtifact }) {
  const direction = artifactDirection(artifact)
  const isRtl = direction === 'rtl'
  const directionalStyle = { direction, textAlign: isRtl ? 'right' : 'left' } satisfies CSSProperties
  const [submittedActionId, setSubmittedActionId] = useState<string | null>(null)
  const preview = artifact.previewSummary
  const startTime = artifact.proposedStartTime || preview.scheduledTime

  const submitAction = (action: HermesUiFlowStateNextBlockArtifact['actions'][number]) => {
    if (!action.submitText) {
      return
    }

    requestComposerSubmit(action.submitText, { target: 'main' })
    setSubmittedActionId(action.id)
  }

  return (
    <section
      aria-label={artifact.title || (isRtl ? 'הבלוק הבא' : 'Next block')}
      className={cn(
        'my-3 overflow-hidden rounded-xl border border-border/80 bg-muted/25 shadow-[0_0.0625rem_0.125rem_color-mix(in_srgb,#000_10%,transparent)]',
        isRtl ? 'text-right' : 'text-left'
      )}
      data-hermes-ui-artifact="flowstate-next-block"
      dir={direction}
      style={directionalStyle}
    >
      <div className="border-b border-border/65 px-3 py-2.5">
        <div className={cn('flex min-w-0 items-start justify-between gap-3', isRtl && 'flex-row-reverse')}>
          <div className="min-w-0">
            {artifact.title && (
              <h3 className="m-0 text-[0.8125rem] leading-snug font-semibold text-foreground" dir={direction} style={directionalStyle}>
                {artifact.title}
              </h3>
            )}
            <p className="m-0 mt-1 text-[0.75rem] leading-relaxed text-muted-foreground" dir={direction} style={directionalStyle}>
              {preview.scheduledDate} · {startTime}
            </p>
          </div>
          <span className="shrink-0 rounded-md border border-border/70 bg-background/45 px-1.5 py-0.5 text-[0.6875rem] leading-none font-medium text-muted-foreground tabular-nums">
            {formatDurationMinutes(artifact.durationMinutes, isRtl)}
          </span>
        </div>
      </div>

      <div className="space-y-2.5 px-3 py-3" dir={direction} style={directionalStyle}>
        <div>
          <div className="text-[0.8125rem] leading-relaxed font-semibold text-foreground wrap-anywhere" dir={direction} style={directionalStyle}>
            {artifact.task.title}
          </div>
          <div className="mt-1 text-[0.75rem] leading-relaxed text-muted-foreground" dir={direction} style={directionalStyle}>
            {isRtl ? 'תאריך יעד' : 'Due'}: {artifact.task.dueDate || (isRtl ? 'אין' : 'none')} · {isRtl ? 'דחיפות' : 'Priority'}:{' '}
            {formatPriority(artifact.task.priority, isRtl)}
          </div>
        </div>

        <div className="rounded-md border border-border/55 bg-background/35 px-2.5 py-2">
          <div className="text-[0.7rem] font-medium text-muted-foreground" dir={direction} style={directionalStyle}>
            {isRtl ? 'מספיק כדי לסיים את הבלוק' : 'Done enough for this block'}
          </div>
          <div className="mt-1 text-[0.78rem] leading-relaxed text-foreground wrap-anywhere" dir={direction} style={directionalStyle}>
            <PlainTextBlocks text={artifact.doneEnough} />
          </div>
        </div>

        <div className="text-[0.75rem] leading-relaxed text-muted-foreground wrap-anywhere" dir={direction} style={directionalStyle}>
          <PlainTextBlocks text={artifact.rationale} />
        </div>

        <div className="text-[0.72rem] leading-relaxed text-muted-foreground/90" dir={direction} style={directionalStyle}>
          {isRtl ? 'Preview מוצע' : 'Proposed preview'}: {preview.scheduledDate} · {preview.scheduledTime} ·{' '}
          {formatDurationMinutes(preview.duration, isRtl)}
        </div>
      </div>

      <div className={cn('flex flex-wrap gap-1.5 border-t border-border/65 px-3 py-2', isRtl && 'justify-end')}>
        {artifact.actions.map(action => (
          <button
            className="rounded-md border border-border/80 bg-background/45 px-2 py-1 text-[0.75rem] font-medium text-foreground hover:bg-muted/70"
            key={action.id}
            onClick={() => submitAction(action)}
            type="button"
          >
            {submittedActionId === action.id ? (isRtl ? 'נשלח ל־Hermes' : 'Sent to Hermes') : action.label}
          </button>
        ))}
      </div>
    </section>
  )
}

export default function HermesUiArtifactRenderer({ code }: RichFenceProps) {
  const result = parseHermesUiArtifact(code)

  if (!result.ok) {
    return null
  }

  if (result.artifact.type === 'checklist' || result.artifact.type === 'questionnaire') {
    return <ChecklistArtifactCard artifact={result.artifact} />
  }

  if (result.artifact.type === 'task-triage') {
    return <TaskTriageArtifactCard artifact={result.artifact} />
  }

  if (result.artifact.type === 'flowstate-task-batch') {
    return <FlowStateTaskBatchCard artifact={result.artifact} />
  }

  if (result.artifact.type === 'flowstate-next-block') {
    return <FlowStateNextBlockCard artifact={result.artifact} />
  }

  return null
}
