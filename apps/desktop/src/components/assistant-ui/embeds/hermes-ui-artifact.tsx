'use client'

import { type CSSProperties, type ReactNode, useId, useMemo, useState } from 'react'

import { requestComposerSubmit } from '@/app/chat/composer/focus'
import {
  buildHermesUiFormResponse,
  HERMES_UI_TASK_BREAKDOWN_LIMITS,
  type HermesUiChecklistArtifact,
  type HermesUiDayTimelineArtifact,
  type HermesUiFlowStateBatchArtifact,
  type HermesUiFlowStateNextBlockArtifact,
  type HermesUiFlowStatePlanningSessionArtifact,
  type HermesUiFormArtifact,
  type HermesUiFormField,
  type HermesUiFormValue,
  type HermesUiMiniKanbanArtifact,
  type HermesUiMutationPreviewArtifact,
  type HermesUiPlanningFunnelArtifact,
  type HermesUiQuestionnaireArtifact,
  type HermesUiTaskBreakdownArtifact,
  type HermesUiTaskContextArtifact,
  type HermesUiTaskGraphArtifact,
  type HermesUiTaskPriority,
  type HermesUiTaskTableArtifact,
  type HermesUiTaskTriageArtifact,
  type HermesUiTriageDecision,
  type HermesUiUrgencyEnergyMatrixArtifact,
  type HermesUiWorkloadBarsArtifact,
  isCanonical24HourTime,
  parseHermesUiArtifact,
  parseHermesUiTaskBreakdownDraftSteps,
  stableArtifactStorageKey
} from '@/lib/hermes-ui-artifacts'
import { readKey, writeKey } from '@/lib/storage'
import { cn } from '@/lib/utils'

import { DailyPlanningListCard } from './daily-planning-list-card'
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
    | HermesUiDayTimelineArtifact
    | HermesUiFlowStateBatchArtifact
    | HermesUiFlowStateNextBlockArtifact
    | HermesUiFlowStatePlanningSessionArtifact
    | HermesUiFormArtifact
    | HermesUiMiniKanbanArtifact
    | HermesUiMutationPreviewArtifact
    | HermesUiPlanningFunnelArtifact
    | HermesUiQuestionnaireArtifact
    | HermesUiTaskContextArtifact
    | HermesUiTaskBreakdownArtifact
    | HermesUiTaskGraphArtifact
    | HermesUiTaskTableArtifact
    | HermesUiTaskTriageArtifact
    | HermesUiUrgencyEnergyMatrixArtifact
    | HermesUiWorkloadBarsArtifact
): 'ltr' | 'rtl' {
  if (artifact.direction === 'rtl' || artifact.direction === 'ltr') {
    return artifact.direction
  }

  const taskText = artifact.type === 'task-triage' ? [artifact.task.title, artifact.task.status] : []
  const batchText = artifact.type === 'flowstate-task-batch' ? artifact.tasks.flatMap(task => [task.title, task.status, task.rationale]) : []

  const nextBlockText =
    artifact.type === 'flowstate-next-block' ? [artifact.task.title, artifact.doneEnough, artifact.rationale] : []

  const planningSessionText =
    artifact.type === 'flowstate-planning-session'
      ? [
          artifact.mode,
          ...artifact.categories.flatMap(category => [
            category.label,
            category.recommendation,
            ...category.examples.map(example => example.title)
          ]),
          ...(artifact.nextBlock ? [artifact.nextBlock.title, artifact.nextBlock.doneEnough, artifact.nextBlock.rationale] : []),
          ...artifact.tasks.flatMap(task => [task.title, task.status, task.rationale])
        ]
      : []

  const funnelText =
    artifact.type === 'planning-funnel' ? artifact.steps.flatMap(step => [step.label, step.description]) : []

  const taskContextText =
    artifact.type === 'task-context'
      ? [
          artifact.task.title,
          artifact.meaning,
          artifact.progress,
          ...(artifact.connections || []),
          ...(artifact.waitingOn || []),
          ...(artifact.unknowns || [])
        ]
      : []

  const taskBreakdownText =
    artifact.type === 'task-breakdown'
      ? [
          artifact.task.title,
          artifact.targetOutcome,
          artifact.stoppingRule,
          ...artifact.steps.flatMap(step => [step.title, step.doneEnough])
        ]
      : []

  const taskTableText =
    artifact.type === 'task-table'
      ? artifact.rows.flatMap(row => [row.title, row.context, row.nextStep])
      : []

  const kanbanText =
    artifact.type === 'mini-kanban'
      ? artifact.lanes.flatMap(lane => [lane.title, lane.description, ...lane.tasks.flatMap(task => [task.title, task.note])])
      : []

  const timelineText =
    artifact.type === 'day-timeline'
      ? artifact.blocks.flatMap(block => [block.label, block.doneEnough])
      : []

  const mutationText =
    artifact.type === 'mutation-preview'
      ? artifact.changes.flatMap(change => [change.title, ...(change.untouched || [])])
      : []

  const matrixText =
    artifact.type === 'urgency-energy-matrix'
      ? artifact.cells.flatMap(cell => [cell.label, ...cell.tasks.map(task => task.title)])
      : []

  const workloadText =
    artifact.type === 'workload-bars' ? artifact.bars.flatMap(bar => [bar.label, bar.note]) : []

  const graphText =
    artifact.type === 'task-graph'
      ? [...artifact.nodes.map(node => node.label), ...artifact.edges.map(edge => edge.label)]
      : []

  const itemText =
    artifact.type === 'checklist' || artifact.type === 'questionnaire'
      ? artifact.items.flatMap(item => [item.label, item.description])
      : []

  const formText = artifact.type === 'form' ? artifact.fields.flatMap(field => [field.label, field.description]) : []

  const sample = [
    artifact.title,
    artifact.description,
    ...taskText,
    ...batchText,
    ...nextBlockText,
    ...planningSessionText,
    ...funnelText,
    ...taskContextText,
    ...taskBreakdownText,
    ...taskTableText,
    ...kanbanText,
    ...timelineText,
    ...mutationText,
    ...matrixText,
    ...workloadText,
    ...graphText,
    ...itemText
    , ...formText
  ]
    .filter(Boolean)
    .join('\n')

  return hasRtlText(sample) ? 'rtl' : 'ltr'
}

function readFormDraft(key: string, fields: readonly HermesUiFormField[]): Record<string, HermesUiFormValue> {
  const raw = readKey(key)
  const defaults: Record<string, HermesUiFormValue> = {}

  for (const field of fields) {
    if (field.defaultValue !== undefined) {
      defaults[field.id] = field.defaultValue
    }
  }

  if (!raw) {
    return defaults
  }

  try {
    const parsed = JSON.parse(raw)

    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return {}
    }

    const entries: Array<[string, HermesUiFormValue]> = []

    for (const field of fields) {
      const value = parsed[field.id]

      if (field.type === 'boolean' && typeof value === 'boolean') {
        entries.push([field.id, value])
      }

      if (field.type === 'multi-choice' && Array.isArray(value) && value.every(item => typeof item === 'string')) {
        entries.push([field.id, value])
      }

      if (field.type !== 'boolean' && field.type !== 'multi-choice' && typeof value === 'string') {
        entries.push([field.id, value])
      }
    }

    return { ...defaults, ...Object.fromEntries(entries) }
  } catch {
    return defaults
  }
}

function formValueMissing(field: HermesUiFormField, value: HermesUiFormValue | undefined): boolean {
  if (!field.required) {
    return false
  }

  if (field.type === 'boolean') {
    return value !== true
  }

  if (Array.isArray(value)) {
    return value.length === 0
  }

  return typeof value !== 'string' || value.trim().length === 0
}

function formValueInvalid(field: HermesUiFormField, value: HermesUiFormValue | undefined): boolean {
  if (field.type === 'time' && typeof value === 'string' && value.length > 0) {
    return !isCanonical24HourTime(value)
  }

  return formValueMissing(field, value)
}

export function FormArtifactCard({ artifact }: { artifact: HermesUiFormArtifact }) {
  const direction = artifactDirection(artifact)
  const storageKey = useMemo(() => `${stableArtifactStorageKey(artifact)}:draft`, [artifact])
  const [values, setValues] = useState<Record<string, HermesUiFormValue>>(() => readFormDraft(storageKey, artifact.fields))
  const [showErrors, setShowErrors] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const updateValue = (id: string, value: HermesUiFormValue) => {
    const next = { ...values, [id]: value }
    setValues(next)
    writeKey(storageKey, JSON.stringify(next))
  }

  const submit = () => {
    if (submitting) {
      return
    }

    if (artifact.fields.some(field => formValueInvalid(field, values[field.id]))) {
      setShowErrors(true)

      return
    }

    setSubmitting(true)
    const response = buildHermesUiFormResponse(artifact, values)

    requestComposerSubmit(`Hermes UI form response:\n${JSON.stringify(response)}`, {
      allowWhileBusy: true,
      hidden: true,
      target: 'main'
    })
  }

  return (
    <section aria-label={artifact.title || 'Interactive form'} className="my-3 rounded-xl border border-border/80 bg-muted/25 p-3" data-hermes-ui-artifact="form" dir={direction}>
      {artifact.title && <h3 className="text-sm font-semibold text-foreground">{artifact.title}</h3>}
      {artifact.description && <p className="mt-1 text-xs text-muted-foreground">{artifact.description}</p>}
      <div className="mt-3 space-y-3">
        {artifact.fields.map(field => {
          const value = values[field.id]
          const invalid = showErrors && formValueInvalid(field, value)
          const common = { 'aria-invalid': invalid, id: `hermes-form-${artifact.id || 'form'}-${field.id}` }

          return (
            <div key={field.id}>
              <label className="block text-xs font-medium text-foreground" htmlFor={common.id}>{field.label}{field.required ? ' *' : ''}</label>
              {field.description && <p className="mb-1 text-[0.7rem] text-muted-foreground">{field.description}</p>}
              {field.type === 'long-text' ? (
                <textarea {...common} className="mt-1 min-h-20 w-full rounded-md border bg-background px-2 py-1.5 text-sm" onChange={event => updateValue(field.id, event.target.value)} placeholder={field.placeholder} value={typeof value === 'string' ? value : ''} />
              ) : field.type === 'single-choice' ? (
                <div className="mt-1 space-y-1">{field.options?.map(option => <label className="flex items-center gap-2 text-sm" key={option.value}><input aria-label={option.label} checked={value === option.value} name={common.id} onChange={() => updateValue(field.id, option.value)} type="radio" />{option.label}</label>)}</div>
              ) : field.type === 'multi-choice' ? (
                <div className="mt-1 space-y-1">{field.options?.map(option => {
                  const selected = Array.isArray(value) ? value : []

                  return <label className="flex items-center gap-2 text-sm" key={option.value}><input aria-label={option.label} checked={selected.includes(option.value)} onChange={event => updateValue(field.id, event.target.checked ? [...selected, option.value] : selected.filter(item => item !== option.value))} type="checkbox" />{option.label}</label>
                })}</div>
              ) : field.type === 'boolean' ? (
                <input {...common} aria-label={field.label} checked={value === true} className="mt-1" onChange={event => updateValue(field.id, event.target.checked)} type="checkbox" />
              ) : field.type === 'time' ? (
                <input {...common} aria-label={field.label} className="mt-1 w-full rounded-md border bg-background px-2 py-1.5 font-mono text-sm tabular-nums" dir="ltr" inputMode="numeric" maxLength={5} onChange={event => updateValue(field.id, event.target.value)} placeholder={field.placeholder || 'HH:mm'} type="text" value={typeof value === 'string' ? value : ''} />
              ) : (
                <input {...common} aria-label={field.label} className="mt-1 w-full rounded-md border bg-background px-2 py-1.5 text-sm" onChange={event => updateValue(field.id, event.target.value)} placeholder={field.placeholder} type={field.type === 'short-text' ? 'text' : field.type} value={typeof value === 'string' ? value : ''} />
              )}
              {invalid && <p className="mt-1 text-xs text-destructive">{field.type === 'time' && typeof value === 'string' && value.length > 0 ? (direction === 'rtl' ? 'יש להזין שעה בפורמט 24 שעות (HH:mm)' : 'Use 24-hour time (HH:mm)') : 'שדה חובה'}</p>}
            </div>
          )
        })}
      </div>
      <button className="mt-3 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-60" disabled={submitting} onClick={submit} type="button">{artifact.submitLabel || 'Submit'}</button>
    </section>
  )
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


function planningModeLabel(mode: HermesUiFlowStatePlanningSessionArtifact['mode'], isRtl: boolean): string {
  const labels: Record<HermesUiFlowStatePlanningSessionArtifact['mode'], { ltr: string; rtl: string }> = {
    'day-start': { ltr: 'Day start', rtl: 'פתיחת יום' },
    'end-of-day': { ltr: 'End of day', rtl: 'סוף יום' },
    'overload-relief': { ltr: 'Overload relief', rtl: 'הורדת עומס' },
    'quick-triage': { ltr: 'Quick triage', rtl: 'מיון מהיר' }
  }

  return labels[mode][isRtl ? 'rtl' : 'ltr']
}

export function FlowStatePlanningSessionCard({ artifact }: { artifact: HermesUiFlowStatePlanningSessionArtifact }) {
  const { direction, directionalStyle, isRtl } = useArtifactDirection(artifact)

  const batchArtifact = {
    description: isRtl
      ? 'בחרו היום / לא היום / לדחות / דיון, עדכנו תאריך או דחיפות, ואז שלחו אל Hermes ל-preview לפני שינוי אמיתי.'
      : 'Pick today / not today / defer / discuss, edit date or priority, then send to Hermes for preview before any real change.',
    direction: artifact.direction,
    id: artifact.id ? `${artifact.id}-decisions` : undefined,
    tasks: artifact.tasks,
    title: isRtl ? 'החלטות למשימות' : 'Task decisions',
    type: 'flowstate-task-batch' as const
  }

  return (
    <ArtifactShell artifact={artifact} label={isRtl ? 'תכנון FlowState' : 'FlowState planning'}>
      <div className="space-y-3 px-3 py-3" dir={direction} style={directionalStyle}>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="rounded-md border border-border/70 bg-background/45 px-2 py-1 text-[0.68rem] font-medium text-muted-foreground">
            {planningModeLabel(artifact.mode, isRtl)}
          </span>
          <span className="rounded-md border border-border/70 bg-background/45 px-2 py-1 text-[0.68rem] font-medium text-muted-foreground">
            {artifact.tasks.length} {isRtl ? 'משימות מומלצות' : 'recommended tasks'}
          </span>
        </div>

        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {artifact.categories.map(category => (
            <section className="rounded-md border border-border/60 bg-background/30 px-2.5 py-2" key={category.id}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-[0.78rem] font-semibold text-foreground wrap-anywhere">{category.label}</div>
                  <div className="mt-0.5 text-[0.66rem] text-muted-foreground">
                    {category.tone} · {category.count}
                  </div>
                </div>
              </div>
              <div className="mt-1.5 text-[0.7rem] leading-relaxed text-muted-foreground wrap-anywhere">
                <PlainTextBlocks text={category.recommendation} />
              </div>
              {category.examples.length > 0 && (
                <div className="mt-2 space-y-1">
                  {category.examples.map(example => (
                    <div className="rounded border border-border/45 px-2 py-1 text-[0.68rem]" key={example.id}>
                      <div className="font-medium text-foreground wrap-anywhere">{example.title}</div>
                      <div className="text-muted-foreground">
                        {example.dueDate || unknownLabel(isRtl)} · {priorityText(example.priority, isRtl)}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>
          ))}
        </div>

        {artifact.nextBlock && (
          <section className="rounded-lg border border-emerald-500/35 bg-emerald-500/10 px-3 py-2.5">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="text-[0.8rem] font-semibold text-foreground wrap-anywhere">{artifact.nextBlock.title}</div>
                <div className="mt-0.5 text-[0.68rem] text-muted-foreground">
                  {formatDurationMinutes(artifact.nextBlock.durationMinutes, isRtl)} · {artifact.nextBlock.taskIds.join(', ')}
                </div>
              </div>
              <span className="rounded-md border border-emerald-500/40 bg-background/35 px-2 py-1 text-[0.65rem] font-medium text-muted-foreground">
                {isRtl ? 'הבלוק הבא' : 'Next block'}
              </span>
            </div>
            <div className="mt-2 rounded-md border border-border/45 bg-background/25 px-2 py-1.5 text-[0.72rem] leading-relaxed text-foreground wrap-anywhere">
              <span className="font-medium text-muted-foreground">{isRtl ? 'מספיק לסיום' : 'Done enough'}: </span>
              <PlainTextBlocks text={artifact.nextBlock.doneEnough} />
            </div>
            <div className="mt-1.5 text-[0.7rem] leading-relaxed text-muted-foreground wrap-anywhere">
              <PlainTextBlocks text={artifact.nextBlock.rationale} />
            </div>
          </section>
        )}
      </div>
      <div className="px-3 pb-3">
        <FlowStateTaskBatchCard artifact={batchArtifact} />
      </div>
    </ArtifactShell>
  )
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

const FUNNEL_STATUS_CLASSES: Record<string, string> = {
  blocked: 'border-destructive/60 bg-destructive/10 text-destructive',
  current: 'border-foreground bg-foreground text-background',
  done: 'border-emerald-500/50 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
  pending: 'border-border/70 bg-background/45 text-muted-foreground'
}

function funnelStatusLabel(status: HermesUiPlanningFunnelArtifact['steps'][number]['status'], isRtl: boolean): string {
  if (status === 'done') {
    return isRtl ? 'בוצע' : 'Done'
  }

  if (status === 'current') {
    return isRtl ? 'עכשיו' : 'Now'
  }

  if (status === 'blocked') {
    return isRtl ? 'תקוע' : 'Blocked'
  }

  return isRtl ? 'פתוח' : 'Open'
}

export function PlanningFunnelCard({ artifact }: { artifact: HermesUiPlanningFunnelArtifact }) {
  const direction = artifactDirection(artifact)
  const isRtl = direction === 'rtl'
  const directionalStyle = { direction, textAlign: isRtl ? 'right' : 'left' } satisfies CSSProperties

  return (
    <section
      aria-label={artifact.title || (isRtl ? 'משפך תכנון' : 'Planning funnel')}
      className={cn(
        'my-3 overflow-hidden rounded-xl border border-border/80 bg-muted/25 shadow-[0_0.0625rem_0.125rem_color-mix(in_srgb,#000_10%,transparent)]',
        isRtl ? 'text-right' : 'text-left'
      )}
      data-hermes-ui-artifact="planning-funnel"
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

      <div className={cn('flex flex-col gap-2 px-3 py-3', isRtl && 'items-stretch')}>
        {artifact.steps.map((step, index) => {
          const status = step.status || 'pending'

          return (
            <div key={step.id}>
              <div className={cn('flex items-start gap-2', isRtl && 'flex-row-reverse')} dir={direction} style={directionalStyle}>
                <span
                  className={cn(
                    'mt-0.5 inline-flex size-6 shrink-0 items-center justify-center rounded-full border text-[0.68rem] font-semibold tabular-nums',
                    FUNNEL_STATUS_CLASSES[status]
                  )}
                >
                  {index + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5" dir={direction}>
                    <span className="text-[0.8125rem] leading-relaxed font-semibold text-foreground wrap-anywhere">{step.label}</span>
                    <span className="rounded-md border border-border/60 bg-background/35 px-1.5 py-0.5 text-[0.65rem] leading-none text-muted-foreground">
                      {funnelStatusLabel(step.status, isRtl)}
                    </span>
                  </div>
                  {step.description && (
                    <div className="mt-0.5 text-[0.72rem] leading-relaxed text-muted-foreground wrap-anywhere" dir={direction} style={directionalStyle}>
                      <PlainTextBlocks text={step.description} />
                    </div>
                  )}
                </div>
              </div>
              {index < artifact.steps.length - 1 && (
                <div className={cn('py-1 text-center text-[0.7rem] text-muted-foreground/70', isRtl && 'text-center')}>↓</div>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}

function ContextLineList({ emptyLabel, items }: { emptyLabel: string; items?: string[] }) {
  if (!items?.length) {
    return <span className="text-muted-foreground/70">{emptyLabel}</span>
  }

  return (
    <span className="space-y-0.5">
      {items.map((item, index) => (
        <span className="block" key={`${index}:${item.slice(0, 16)}`}>
          {item}
        </span>
      ))}
    </span>
  )
}

export function TaskContextCard({ artifact }: { artifact: HermesUiTaskContextArtifact }) {
  const direction = artifactDirection(artifact)
  const isRtl = direction === 'rtl'
  const directionalStyle = { direction, textAlign: isRtl ? 'right' : 'left' } satisfies CSSProperties
  const [submittedActionId, setSubmittedActionId] = useState<string | null>(null)

  const submitAction = (action: NonNullable<HermesUiTaskContextArtifact['actions']>[number]) => {
    if (!action.submitText) {
      return
    }

    requestComposerSubmit(action.submitText, { target: 'main' })
    setSubmittedActionId(action.id)
  }

  const empty = isRtl ? 'עדיין לא מובן' : 'Not understood yet'

  return (
    <section
      aria-label={artifact.title || artifact.task.title}
      className={cn(
        'my-3 overflow-hidden rounded-xl border border-border/80 bg-muted/25 shadow-[0_0.0625rem_0.125rem_color-mix(in_srgb,#000_10%,transparent)]',
        isRtl ? 'text-right' : 'text-left'
      )}
      data-hermes-ui-artifact="task-context"
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
            <div className="mt-1 text-[0.8125rem] leading-relaxed font-semibold text-foreground wrap-anywhere" dir={direction} style={directionalStyle}>
              {artifact.task.title}
            </div>
          </div>
          <span className="shrink-0 rounded-md border border-border/70 bg-background/45 px-1.5 py-0.5 text-[0.6875rem] leading-none font-medium text-muted-foreground tabular-nums">
            {artifact.task.dueDate || (isRtl ? 'אין תאריך' : 'no date')}
          </span>
        </div>
      </div>

      <div className="grid gap-2 px-3 py-3 sm:grid-cols-2" dir={direction} style={directionalStyle}>
        {[
          [isRtl ? 'למה זה קיים' : 'Why it exists', artifact.meaning || empty],
          [isRtl ? 'מה נחשב התקדמות' : 'Progress means', artifact.progress || empty]
        ].map(([label, value]) => (
          <div className="rounded-md border border-border/55 bg-background/35 px-2.5 py-2" key={label}>
            <div className="text-[0.68rem] font-medium text-muted-foreground">{label}</div>
            <div className="mt-1 text-[0.76rem] leading-relaxed text-foreground wrap-anywhere">
              <PlainTextBlocks text={value} />
            </div>
          </div>
        ))}
        <div className="rounded-md border border-border/55 bg-background/35 px-2.5 py-2">
          <div className="text-[0.68rem] font-medium text-muted-foreground">{isRtl ? 'מתחבר אל' : 'Connects to'}</div>
          <div className="mt-1 text-[0.76rem] leading-relaxed text-foreground wrap-anywhere">
            <ContextLineList emptyLabel={empty} items={artifact.connections} />
          </div>
        </div>
        <div className="rounded-md border border-border/55 bg-background/35 px-2.5 py-2">
          <div className="text-[0.68rem] font-medium text-muted-foreground">{isRtl ? 'חסר כדי להחליט' : 'Unknown before deciding'}</div>
          <div className="mt-1 text-[0.76rem] leading-relaxed text-foreground wrap-anywhere">
            <ContextLineList emptyLabel={empty} items={artifact.unknowns} />
          </div>
        </div>
      </div>

      {artifact.waitingOn?.length ? (
        <div className="border-t border-border/45 px-3 py-2 text-[0.75rem] leading-relaxed text-muted-foreground" dir={direction} style={directionalStyle}>
          <span className="font-medium text-foreground">{isRtl ? 'מחכה ל' : 'Waiting on'}: </span>
          <ContextLineList emptyLabel={empty} items={artifact.waitingOn} />
        </div>
      ) : null}

      {artifact.actions?.length ? (
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
      ) : null}
    </section>
  )
}

const BREAKDOWN_SCOPE_LABELS: Record<HermesUiTaskBreakdownArtifact['scope'], { ltr: string; rtl: string }> = {
  'full-delivery': { ltr: 'Full delivery', rtl: 'מסירה מלאה' },
  'next-move': { ltr: 'Next move', rtl: 'הצעד הבא' },
  'working-session': { ltr: 'Working session', rtl: 'סשן עבודה' }
}

function readTaskBreakdownDraft(
  storageKey: string,
  artifact: HermesUiTaskBreakdownArtifact
): HermesUiTaskBreakdownArtifact['steps'] {
  const fallback = () => artifact.steps.map(step => ({ ...step }))
  const raw = readKey(storageKey)

  if (!raw) {return fallback()}

  try {
    const steps = JSON.parse(raw)
    const draft = parseHermesUiTaskBreakdownDraftSteps(steps)

    return draft || fallback()
  } catch {
    return fallback()
  }
}

export function TaskBreakdownCard({ artifact }: { artifact: HermesUiTaskBreakdownArtifact }) {
  const direction = artifactDirection(artifact)
  const isRtl = direction === 'rtl'
  const directionalStyle = { direction, textAlign: isRtl ? 'right' : 'left' } satisfies CSSProperties
  const storageKey = useMemo(() => stableArtifactStorageKey(artifact), [artifact])
  const [steps, setSteps] = useState(() => readTaskBreakdownDraft(storageKey, artifact))
  const [submitted, setSubmitted] = useState(false)

  const replaceSteps = (
    updater: (current: HermesUiTaskBreakdownArtifact['steps']) => HermesUiTaskBreakdownArtifact['steps']
  ) => {
    setSteps(current => {
      const next = updater(current)
      writeKey(storageKey, JSON.stringify(next))

      return next
    })
  }

  const updateStep = (id: string, patch: Partial<HermesUiTaskBreakdownArtifact['steps'][number]>) => {
    setSubmitted(false)
    replaceSteps(current => current.map(step => (step.id === id ? { ...step, ...patch } : step)))
  }

  const moveStep = (index: number, offset: -1 | 1) => {
    const target = index + offset

    if (target < 0 || target >= steps.length) {return}
    setSubmitted(false)
    replaceSteps(current => {
      const next = [...current]
      const [step] = next.splice(index, 1)
      next.splice(target, 0, step)

      return next
    })
  }

  const removeStep = (id: string) => {
    if (steps.length <= 1) {return}
    setSubmitted(false)
    replaceSteps(current => current.filter(step => step.id !== id))
  }

  const addStep = () => {
    if (steps.length >= 12) {return}
    const used = new Set(steps.map(step => step.id))
    let index = steps.length + 1

    while (used.has(`draft-${index}`)) {index += 1}
    setSubmitted(false)
    replaceSteps(current => [
      ...current,
      {
        doneEnough: '',
        id: `draft-${index}`,
        title: ''
      }
    ])
  }

  const valid = steps.every(step => step.title.trim() && step.doneEnough.trim())

  const submit = () => {
    if (!valid) {return}

    const response = [
      `I revised the task breakdown for taskId=${artifact.task.id}.`,
      `scope=${artifact.scope}`,
      artifact.targetOutcome ? `Target outcome: ${artifact.targetOutcome}` : '',
      artifact.stoppingRule ? `Stopping rule: ${artifact.stoppingRule}` : '',
      'Ordered steps:',
      ...steps.map(
        (step, index) =>
          `${index + 1}. ${step.title.trim()} | done enough: ${step.doneEnough.trim()}` +
          (step.estimateMinutes ? ` | estimate: ${step.estimateMinutes} minutes` : '') +
          (step.optional ? ' | optional' : '')
      ),
      'Use this exact revision to regenerate the preview; do not apply or create FlowState subtasks until I explicitly approve that new preview.'
    ]
      .filter(Boolean)
      .join('\n')

    requestComposerSubmit(response, { target: 'main' })
    setSubmitted(true)
  }

  const labels = isRtl
    ? {
        add: 'הוסף צעד', doneEnough: 'מה נחשב מספיק', down: 'העבר את', optional: 'אופציונלי',
        remove: 'הסר את', step: 'צעד', stopping: 'כלל עצירה', target: 'תוצאה רצויה', up: 'העבר את'
      }
    : {
        add: 'Add step', doneEnough: 'Done enough', down: 'Move', optional: 'Optional',
        remove: 'Remove', step: 'Step', stopping: 'Stopping rule', target: 'Target outcome', up: 'Move'
      }

  return (
    <section
      aria-label={artifact.title || artifact.task.title}
      className={cn('my-3 overflow-hidden rounded-xl border border-border/80 bg-muted/25', isRtl ? 'text-right' : 'text-left')}
      data-hermes-ui-artifact="task-breakdown"
      dir={direction}
      style={directionalStyle}
    >
      <div className="border-b border-border/65 px-3 py-2.5">
        <div className={cn('flex items-start justify-between gap-3', isRtl && 'flex-row-reverse')}>
          <div className="min-w-0">
            <h3 className="m-0 text-[0.8125rem] leading-snug font-semibold text-foreground">
              {artifact.title || artifact.task.title}
            </h3>
            {artifact.title && <div className="mt-1 text-[0.75rem] text-muted-foreground">{artifact.task.title}</div>}
          </div>
          <span className="shrink-0 rounded-md border border-border/70 bg-background/45 px-2 py-1 text-[0.68rem] font-medium text-muted-foreground">
            {BREAKDOWN_SCOPE_LABELS[artifact.scope][isRtl ? 'rtl' : 'ltr']}
          </span>
        </div>
        {artifact.description && <p className="m-0 mt-1 text-[0.72rem] text-muted-foreground">{artifact.description}</p>}
      </div>

      {(artifact.targetOutcome || artifact.stoppingRule) && (
        <div className="grid gap-2 border-b border-border/55 px-3 py-2.5 sm:grid-cols-2">
          {artifact.targetOutcome && (
            <div className="rounded-md border border-border/55 bg-background/35 px-2.5 py-2">
              <div className="text-[0.66rem] font-medium text-muted-foreground">{labels.target}</div>
              <div className="mt-1 text-[0.74rem] leading-relaxed text-foreground">{artifact.targetOutcome}</div>
            </div>
          )}
          {artifact.stoppingRule && (
            <div className="rounded-md border border-border/55 bg-background/35 px-2.5 py-2">
              <div className="text-[0.66rem] font-medium text-muted-foreground">{labels.stopping}</div>
              <div className="mt-1 text-[0.74rem] leading-relaxed text-foreground">{artifact.stoppingRule}</div>
            </div>
          )}
        </div>
      )}

      <div className="space-y-2 px-3 py-3">
        {steps.map((step, index) => (
          <div className="rounded-lg border border-border/60 bg-background/35 p-2.5" key={step.id}>
            <div className={cn('mb-1.5 flex items-center justify-between gap-2', isRtl && 'flex-row-reverse')}>
              <div className={cn('flex items-center gap-1.5', isRtl && 'flex-row-reverse')}>
                <span className="inline-flex size-5 items-center justify-center rounded-full bg-foreground text-[0.65rem] font-semibold text-background">
                  {index + 1}
                </span>
                <span className="text-[0.68rem] font-medium text-muted-foreground">{labels.step}</span>
                {step.optional && (
                  <span className="rounded border border-border/60 px-1.5 py-0.5 text-[0.62rem] text-muted-foreground">{labels.optional}</span>
                )}
              </div>
              <div className={cn('flex items-center gap-1', isRtl && 'flex-row-reverse')}>
                <button aria-label={`${labels.up} ${step.title} ${isRtl ? 'למעלה' : 'up'}`} disabled={index === 0} onClick={() => moveStep(index, -1)} type="button">↑</button>
                <button aria-label={`${labels.down} ${step.title} ${isRtl ? 'למטה' : 'down'}`} disabled={index === steps.length - 1} onClick={() => moveStep(index, 1)} type="button">↓</button>
                <button aria-label={`${labels.remove} ${step.title}`} disabled={steps.length === 1} onClick={() => removeStep(step.id)} type="button">×</button>
              </div>
            </div>
            <input
              aria-label={`${labels.step} ${index + 1}`}
              className="w-full rounded-md border border-border/70 bg-background px-2 py-1.5 text-[0.76rem] text-foreground"
              maxLength={HERMES_UI_TASK_BREAKDOWN_LIMITS.titleLength}
              onChange={event => updateStep(step.id, { title: event.target.value })}
              value={step.title}
            />
            <input
              aria-label={`${labels.doneEnough} ${index + 1}`}
              className="mt-1.5 w-full rounded-md border border-border/70 bg-background px-2 py-1.5 text-[0.72rem] text-foreground"
              maxLength={HERMES_UI_TASK_BREAKDOWN_LIMITS.doneEnoughLength}
              onChange={event => updateStep(step.id, { doneEnough: event.target.value })}
              placeholder={labels.doneEnough}
              value={step.doneEnough}
            />
          </div>
        ))}
      </div>

      <div className={cn('flex flex-wrap items-center gap-2 border-t border-border/65 px-3 py-2.5', isRtl && 'justify-end')}>
        <button className="rounded-md border border-border/80 bg-background/45 px-2 py-1 text-[0.72rem]" disabled={steps.length >= 12} onClick={addStep} type="button">
          {labels.add}
        </button>
        <button className="rounded-md bg-foreground px-2.5 py-1 text-[0.72rem] font-medium text-background disabled:opacity-45" disabled={!valid} onClick={submit} type="button">
          {submitted ? (isRtl ? 'נשלח ל־Hermes' : 'Sent to Hermes') : artifact.submitLabel || (isRtl ? 'עדכן את הפירוק' : 'Update breakdown')}
        </button>
      </div>
    </section>
  )
}

function ArtifactShell({
  artifact,
  children,
  label
}: {
  artifact:
    | HermesUiDayTimelineArtifact
    | HermesUiFlowStatePlanningSessionArtifact
    | HermesUiMiniKanbanArtifact
    | HermesUiMutationPreviewArtifact
    | HermesUiTaskGraphArtifact
    | HermesUiTaskTableArtifact
    | HermesUiUrgencyEnergyMatrixArtifact
    | HermesUiWorkloadBarsArtifact
  children: ReactNode
  label: string
}) {
  const direction = artifactDirection(artifact)
  const isRtl = direction === 'rtl'
  const directionalStyle = { direction, textAlign: isRtl ? 'right' : 'left' } satisfies CSSProperties

  return (
    <section
      aria-label={artifact.title || label}
      className={cn(
        'my-3 overflow-hidden rounded-xl border border-border/80 bg-muted/25 shadow-[0_0.0625rem_0.125rem_color-mix(in_srgb,#000_10%,transparent)]',
        isRtl ? 'text-right' : 'text-left'
      )}
      data-hermes-ui-artifact={artifact.type}
      dir={direction}
      style={directionalStyle}
    >
      {(artifact.title || artifact.description) && (
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
      )}
      {children}
    </section>
  )
}

function useArtifactDirection(artifact: Parameters<typeof artifactDirection>[0]) {
  const direction = artifactDirection(artifact)
  const isRtl = direction === 'rtl'
  const directionalStyle = { direction, textAlign: isRtl ? 'right' : 'left' } satisfies CSSProperties

  return { direction, directionalStyle, isRtl }
}

function PlanningActionButtons({
  actions,
  isRtl
}: {
  actions?: { id: string; label: string; submitText?: string }[]
  isRtl: boolean
}) {
  const [submittedActionId, setSubmittedActionId] = useState<string | null>(null)

  if (!actions?.length) {
    return null
  }

  return (
    <div className={cn('mt-2 flex flex-wrap gap-1.5', isRtl && 'justify-end')}>
      {actions.map(action => (
        <button
          className="rounded-md border border-border/80 bg-background/45 px-2 py-1 text-[0.7rem] font-medium text-foreground hover:bg-muted/70"
          key={action.id}
          onClick={() => {
            if (action.submitText) {
              requestComposerSubmit(action.submitText, { target: 'main' })
              setSubmittedActionId(action.id)
            }
          }}
          type="button"
        >
          {submittedActionId === action.id ? (isRtl ? 'נשלח ל־Hermes' : 'Sent to Hermes') : action.label}
        </button>
      ))}
    </div>
  )
}

function unknownLabel(isRtl: boolean) {
  return isRtl ? 'לא ידוע' : 'Unknown'
}

function valueLabel(value: string | number | boolean | null | undefined, isRtl: boolean): string {
  if (value === undefined || value === null || value === 'unknown') {
    return unknownLabel(isRtl)
  }

  if (typeof value === 'boolean') {
    return value ? (isRtl ? 'כן' : 'Yes') : (isRtl ? 'לא' : 'No')
  }

  return String(value)
}

function priorityText(priority: HermesUiTaskPriority | undefined, isRtl: boolean): string {
  return priority ? formatPriority(priority, isRtl) : unknownLabel(isRtl)
}

function columnLabel(column: HermesUiTaskTableArtifact['columns'][number], isRtl: boolean): string {
  const labels: Record<typeof column, { ltr: string; rtl: string }> = {
    confidence: { ltr: 'Confidence', rtl: 'ביטחון' },
    context: { ltr: 'Context', rtl: 'הקשר' },
    energy: { ltr: 'Energy', rtl: 'אנרגיה' },
    externality: { ltr: 'Externality', rtl: 'חיצוני' },
    nextStep: { ltr: 'Next step', rtl: 'צעד הבא' },
    task: { ltr: 'Task', rtl: 'משימה' },
    timeSize: { ltr: 'Size', rtl: 'גודל' },
    urgency: { ltr: 'Urgency', rtl: 'דחיפות' }
  }

  return labels[column][isRtl ? 'rtl' : 'ltr']
}

export function TaskTableCard({ artifact }: { artifact: HermesUiTaskTableArtifact }) {
  const { directionalStyle, isRtl } = useArtifactDirection(artifact)

  const cellValue = (row: HermesUiTaskTableArtifact['rows'][number], column: HermesUiTaskTableArtifact['columns'][number]) => {
    if (column === 'task') {
      return (
        <span>
          <span className="block font-semibold text-foreground">{row.title}</span>
          <span className="block text-muted-foreground">
            {row.dueDate || unknownLabel(isRtl)} · {priorityText(row.priority, isRtl)}
          </span>
        </span>
      )
    }

    return valueLabel(row[column], isRtl)
  }

  return (
    <ArtifactShell artifact={artifact} label={isRtl ? 'טבלת משימות' : 'Task table'}>
      <div className="overflow-x-auto px-3 py-3">
        <table className="w-full min-w-[34rem] border-collapse text-[0.74rem]" style={directionalStyle}>
          <thead>
            <tr className="border-b border-border/65 text-muted-foreground">
              {artifact.columns.map(column => (
                <th className="px-2 py-1.5 text-start font-medium" key={column}>
                  {columnLabel(column, isRtl)}
                </th>
              ))}
              <th className="px-2 py-1.5 text-start font-medium">{isRtl ? 'פעולה' : 'Action'}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/45">
            {artifact.rows.map(row => (
              <tr key={row.id}>
                {artifact.columns.map(column => (
                  <td className="max-w-[14rem] px-2 py-2 align-top leading-relaxed wrap-anywhere" key={column}>
                    {cellValue(row, column)}
                  </td>
                ))}
                <td className="px-2 py-2 align-top">
                  <PlanningActionButtons actions={row.actions} isRtl={isRtl} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </ArtifactShell>
  )
}

export function MiniKanbanCard({ artifact }: { artifact: HermesUiMiniKanbanArtifact }) {
  const { direction, directionalStyle, isRtl } = useArtifactDirection(artifact)

  return (
    <ArtifactShell artifact={artifact} label={isRtl ? 'קנבן קצר' : 'Mini kanban'}>
      <div className="grid gap-2 px-3 py-3 md:grid-cols-2 xl:grid-cols-3" dir={direction} style={directionalStyle}>
        {artifact.lanes.map(lane => (
          <section className="min-w-0 rounded-md border border-border/60 bg-background/30" key={lane.id}>
            <div className="border-b border-border/50 px-2.5 py-2">
              <div className="text-[0.78rem] font-semibold text-foreground">{lane.title}</div>
              {lane.description && <div className="mt-0.5 text-[0.68rem] text-muted-foreground">{lane.description}</div>}
            </div>
            <div className="divide-y divide-border/40">
              {lane.tasks.length ? (
                lane.tasks.map(task => (
                  <div className="px-2.5 py-2" key={task.id}>
                    <div className="text-[0.76rem] font-medium leading-relaxed text-foreground wrap-anywhere">{task.title}</div>
                    <div className="mt-0.5 text-[0.68rem] text-muted-foreground">
                      {task.dueDate || unknownLabel(isRtl)} · {priorityText(task.priority, isRtl)} · {valueLabel(task.confidence, isRtl)}
                    </div>
                    {task.note && <div className="mt-1 text-[0.7rem] leading-relaxed text-muted-foreground wrap-anywhere">{task.note}</div>}
                    <PlanningActionButtons actions={task.actions} isRtl={isRtl} />
                  </div>
                ))
              ) : (
                <div className="px-2.5 py-2 text-[0.7rem] text-muted-foreground">{isRtl ? 'אין משימות' : 'No tasks'}</div>
              )}
            </div>
          </section>
        ))}
      </div>
    </ArtifactShell>
  )
}

export function DayTimelineCard({ artifact }: { artifact: HermesUiDayTimelineArtifact }) {
  const { direction, directionalStyle, isRtl } = useArtifactDirection(artifact)

  return (
    <ArtifactShell artifact={artifact} label={isRtl ? 'ציר יום' : 'Day timeline'}>
      <div className="space-y-2 px-3 py-3" dir={direction} style={directionalStyle}>
        <div className="text-[0.72rem] text-muted-foreground">
          {artifact.date}
          {artifact.currentTime ? ` · ${isRtl ? 'עכשיו' : 'Now'} ${artifact.currentTime}` : ''}
        </div>
        {artifact.currentTime && (
          <div className="flex items-center gap-2 text-[0.68rem] text-muted-foreground">
            <span className="h-px flex-1 bg-foreground/35" />
            <span className="tabular-nums">{artifact.currentTime}</span>
            <span className="h-px flex-1 bg-foreground/35" />
          </div>
        )}
        <div className="divide-y divide-border/45 rounded-md border border-border/55 bg-background/25">
          {artifact.blocks.map(block => (
            <div className={cn('flex gap-2 px-2.5 py-2', isRtl && 'flex-row-reverse')} key={block.id}>
              <div className="w-16 shrink-0 text-[0.68rem] text-muted-foreground tabular-nums">
                {block.startTime || (isRtl ? 'צף' : 'Float')}
                {block.endTime ? `-${block.endTime}` : ''}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-[0.78rem] font-semibold text-foreground wrap-anywhere">{block.label}</span>
                  <span className="rounded-md border border-border/60 px-1.5 py-0.5 text-[0.62rem] text-muted-foreground">
                    {valueLabel(block.kind, isRtl)}
                  </span>
                  <span className="rounded-md border border-border/60 px-1.5 py-0.5 text-[0.62rem] text-muted-foreground">
                    {valueLabel(block.status, isRtl)}
                  </span>
                </div>
                {block.doneEnough && <div className="mt-1 text-[0.7rem] text-muted-foreground wrap-anywhere">{block.doneEnough}</div>}
                <PlanningActionButtons actions={block.actions} isRtl={isRtl} />
              </div>
            </div>
          ))}
        </div>
      </div>
    </ArtifactShell>
  )
}

export function MutationPreviewCard({ artifact }: { artifact: HermesUiMutationPreviewArtifact }) {
  const { direction, directionalStyle, isRtl } = useArtifactDirection(artifact)

  return (
    <ArtifactShell artifact={artifact} label={isRtl ? 'תצוגת שינוי לפני אישור' : 'Mutation preview'}>
      <div className="space-y-2 px-3 py-3" dir={direction} style={directionalStyle}>
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2.5 py-2 text-[0.72rem] font-medium text-amber-800 dark:text-amber-200">
          {isRtl ? 'Preview בלבד. לא מתבצע שינוי ב־FlowState מהרכיב הזה.' : 'Preview only. This component does not write to FlowState.'}
        </div>
        {artifact.changes.map(change => (
          <div className="rounded-md border border-border/60 bg-background/30 px-2.5 py-2" key={`${change.taskId}:${change.operation}`}>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[0.78rem] font-semibold text-foreground wrap-anywhere">{change.title}</span>
              <span className="rounded-md border border-border/60 px-1.5 py-0.5 text-[0.62rem] text-muted-foreground">{change.operation}</span>
              <span className="rounded-md border border-border/60 px-1.5 py-0.5 text-[0.62rem] text-muted-foreground">
                {isRtl ? 'סיכון' : 'Risk'}: {valueLabel(change.risk, isRtl)}
              </span>
            </div>
            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              {[
                [isRtl ? 'לפני' : 'Before', change.before],
                [isRtl ? 'אחרי' : 'After', change.after]
              ].map(([label, record]) => (
                <div className="rounded-md border border-border/45 px-2 py-1.5" key={String(label)}>
                  <div className="text-[0.66rem] font-medium text-muted-foreground">{String(label)}</div>
                  <div className="mt-1 space-y-0.5 text-[0.7rem]">
                    {record && Object.keys(record).length ? (
                      Object.entries(record).map(([key, value]) => (
                        <div className="flex justify-between gap-2" key={key}>
                          <span className="text-muted-foreground">{key}</span>
                          <span className="text-foreground tabular-nums">{valueLabel(value, isRtl)}</span>
                        </div>
                      ))
                    ) : (
                      <span className="text-muted-foreground">{unknownLabel(isRtl)}</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
            {change.untouched?.length ? (
              <div className="mt-2 text-[0.68rem] text-muted-foreground">
                {isRtl ? 'לא משתנה' : 'Untouched'}: {change.untouched.join(', ')}
              </div>
            ) : null}
          </div>
        ))}
        <PlanningActionButtons actions={artifact.actions} isRtl={isRtl} />
      </div>
    </ArtifactShell>
  )
}

export function UrgencyEnergyMatrixCard({ artifact }: { artifact: HermesUiUrgencyEnergyMatrixArtifact }) {
  const { direction, directionalStyle, isRtl } = useArtifactDirection(artifact)
  const levels: Array<'high' | 'medium' | 'low'> = ['high', 'medium', 'low']

  return (
    <ArtifactShell artifact={artifact} label={isRtl ? 'מטריצת אנרגיה ודחיפות' : 'Urgency energy matrix'}>
      <div className="px-3 py-3" dir={direction} style={directionalStyle}>
        <div className="grid grid-cols-3 gap-1.5">
          {levels.flatMap(y =>
            (['low', 'medium', 'high'] as const).map(x => {
              const cell = artifact.cells.find(candidate => candidate.x === x && candidate.y === y)

              return (
                <div className="min-h-24 rounded-md border border-border/55 bg-background/25 p-2" key={`${x}:${y}`}>
                  <div className="text-[0.64rem] font-medium text-muted-foreground">
                    {cell?.label || `${artifact.yAxis}: ${y} · ${artifact.xAxis}: ${x}`}
                  </div>
                  <div className="mt-1 space-y-1">
                    {cell?.tasks.length ? (
                      cell.tasks.map(task => (
                        <div className="rounded border border-border/45 px-1.5 py-1 text-[0.68rem]" key={task.id}>
                          <div className="font-medium text-foreground wrap-anywhere">{task.title}</div>
                          <div className="text-muted-foreground">{priorityText(task.priority, isRtl)}</div>
                          <PlanningActionButtons actions={task.actions} isRtl={isRtl} />
                        </div>
                      ))
                    ) : (
                      <span className="text-[0.66rem] text-muted-foreground">{unknownLabel(isRtl)}</span>
                    )}
                  </div>
                </div>
              )
            })
          )}
        </div>
      </div>
    </ArtifactShell>
  )
}

const BAR_TONES: Record<string, string> = {
  danger: 'bg-destructive/70',
  neutral: 'bg-foreground/55',
  success: 'bg-emerald-500/70',
  warning: 'bg-amber-500/75'
}

export function WorkloadBarsCard({ artifact }: { artifact: HermesUiWorkloadBarsArtifact }) {
  const { direction, directionalStyle, isRtl } = useArtifactDirection(artifact)

  return (
    <ArtifactShell artifact={artifact} label={isRtl ? 'עומס עבודה' : 'Workload bars'}>
      <div className="space-y-2 px-3 py-3" dir={direction} style={directionalStyle}>
        {artifact.bars.map(bar => {
          const max = bar.max || Math.max(bar.value, 1)
          const percent = Math.min(100, (bar.value / max) * 100)

          return (
            <div key={bar.id}>
              <div className="flex items-baseline justify-between gap-3 text-[0.74rem]">
                <span className="font-medium text-foreground">{bar.label}</span>
                <span className="text-muted-foreground tabular-nums">
                  {bar.value}
                  {bar.max ? ` / ${bar.max}` : ''}
                </span>
              </div>
              <div className="mt-1 h-2 overflow-hidden rounded-full bg-background/70">
                <div className={cn('h-full rounded-full', BAR_TONES[bar.tone || 'neutral'])} style={{ width: `${percent}%` }} />
              </div>
              {bar.note && <div className="mt-0.5 text-[0.68rem] text-muted-foreground">{bar.note}</div>}
            </div>
          )
        })}
      </div>
    </ArtifactShell>
  )
}

export function TaskGraphCard({ artifact }: { artifact: HermesUiTaskGraphArtifact }) {
  const { direction, directionalStyle, isRtl } = useArtifactDirection(artifact)
  const nodeById = new Map(artifact.nodes.map(node => [node.id, node]))

  return (
    <ArtifactShell artifact={artifact} label={isRtl ? 'קשרי משימות' : 'Task graph'}>
      <div className="space-y-2 px-3 py-3" dir={direction} style={directionalStyle}>
        <div className="flex flex-wrap gap-1.5">
          {artifact.nodes.map(node => (
            <span className="rounded-md border border-border/60 bg-background/35 px-2 py-1 text-[0.7rem]" key={node.id}>
              {node.label}
              {node.kind ? <span className="text-muted-foreground"> · {node.kind}</span> : null}
            </span>
          ))}
        </div>
        <div className="divide-y divide-border/45 rounded-md border border-border/55 bg-background/25">
          {artifact.edges.map(edge => (
            <div className="px-2.5 py-2 text-[0.72rem]" key={`${edge.source}:${edge.target}`}>
              <span className="font-medium text-foreground">{nodeById.get(edge.source)?.label || edge.source}</span>
              <span className="px-1.5 text-muted-foreground">→</span>
              <span className="font-medium text-foreground">{nodeById.get(edge.target)?.label || edge.target}</span>
              {edge.label && <span className="text-muted-foreground"> · {edge.label}</span>}
            </div>
          ))}
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

  if (result.artifact.type === 'checklist' || result.artifact.type === 'questionnaire') {
    return <ChecklistArtifactCard artifact={result.artifact} />
  }

  if (result.artifact.type === 'form') {
    return <FormArtifactCard artifact={result.artifact} />
  }

  if (result.artifact.type === 'task-triage') {
    return <TaskTriageArtifactCard artifact={result.artifact} />
  }

  if (result.artifact.type === 'flowstate-task-batch') {
    return <FlowStateTaskBatchCard artifact={result.artifact} />
  }

  if (result.artifact.type === 'flowstate-planning-session') {
    return <FlowStatePlanningSessionCard artifact={result.artifact} />
  }

  if (result.artifact.type === 'daily-planning-list') {
    return <DailyPlanningListCard artifact={result.artifact} />
  }

  if (result.artifact.type === 'flowstate-next-block') {
    return <FlowStateNextBlockCard artifact={result.artifact} />
  }

  if (result.artifact.type === 'planning-funnel') {
    return <PlanningFunnelCard artifact={result.artifact} />
  }

  if (result.artifact.type === 'task-context') {
    return <TaskContextCard artifact={result.artifact} />
  }

  if (result.artifact.type === 'task-breakdown') {
    return <TaskBreakdownCard artifact={result.artifact} />
  }

  if (result.artifact.type === 'task-table') {
    return <TaskTableCard artifact={result.artifact} />
  }

  if (result.artifact.type === 'mini-kanban') {
    return <MiniKanbanCard artifact={result.artifact} />
  }

  if (result.artifact.type === 'day-timeline') {
    return <DayTimelineCard artifact={result.artifact} />
  }

  if (result.artifact.type === 'mutation-preview') {
    return <MutationPreviewCard artifact={result.artifact} />
  }

  if (result.artifact.type === 'urgency-energy-matrix') {
    return <UrgencyEnergyMatrixCard artifact={result.artifact} />
  }

  if (result.artifact.type === 'workload-bars') {
    return <WorkloadBarsCard artifact={result.artifact} />
  }

  if (result.artifact.type === 'task-graph') {
    return <TaskGraphCard artifact={result.artifact} />
  }

  return null
}
