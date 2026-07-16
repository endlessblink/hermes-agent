'use client'

import { useVirtualizer } from '@tanstack/react-virtual'
import { type CSSProperties, type ReactNode, useEffect, useMemo, useRef, useState } from 'react'

import { requestComposerSubmit } from '@/app/chat/composer/focus'
import {
  buildHermesUiDailyPlanningListResponse,
  createHermesUiDailyPlanningDraft,
  type HermesUiDailyPlanningConflictDecision,
  type HermesUiDailyPlanningDraft,
  type HermesUiDailyPlanningDraftRow,
  type HermesUiDailyPlanningListArtifact,
  type HermesUiDailyPlanningRow,
  type HermesUiDailyPlanningSection
} from '@/lib/hermes-ui-artifacts'
import { cn } from '@/lib/utils'

const VIRTUALIZE_AT = 60
const COLLAPSE_PLAN_AT = 20

type PlanningCardInstance = { setLatest: (latest: boolean) => void; token: symbol }
const planningCardInstances = new Map<string, PlanningCardInstance[]>()

function useLatestPlanningCard(key: string): boolean {
  const token = useRef(Symbol(key))
  const [latest, setLatest] = useState(true)

  useEffect(() => {
    const instanceToken = token.current
    const instances = planningCardInstances.get(key) ?? []
    const instance = { setLatest, token: instanceToken }

    instances.push(instance)
    planningCardInstances.set(key, instances)
    instances.forEach((candidate, index) => candidate.setLatest(index === instances.length - 1))

    return () => {
      const remaining = (planningCardInstances.get(key) ?? []).filter(candidate => candidate.token !== instanceToken)

      if (remaining.length === 0) {
        planningCardInstances.delete(key)
      } else {
        planningCardInstances.set(key, remaining)
        remaining.forEach((candidate, index) => candidate.setLatest(index === remaining.length - 1))
      }
    }
  }, [key])

  return latest
}

function text(isRtl: boolean, rtl: string, ltr: string): string {
  return isRtl ? rtl : ltr
}

function same(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right)
}

function rowContextValue(row: HermesUiDailyPlanningDraftRow): string {
  return row.context[0]?.text ?? ''
}

function visibleRows(section: HermesUiDailyPlanningSection): HermesUiDailyPlanningRow[] {
  if (section.kind !== 'calendar') {return section.rows}

  return [...section.rows].sort((left, right) => {
    const leftTime = left.temporal?.startTime ?? '99:99'
    const rightTime = right.temporal?.startTime ?? '99:99'

    return leftTime.localeCompare(rightTime)
  })
}

function SelectField({
  disabled,
  label,
  onChange,
  options,
  value
}: {
  disabled?: boolean
  label: string
  onChange: (value: string) => void
  options: Array<{ label: string; value: string }>
  value: string
}) {
  return (
    <label className="min-w-32 flex-1 text-xs font-medium text-muted-foreground">
      <span className="mb-1 block">{label.split(' — ')[0]}</span>
      <select
        aria-label={label}
        className="h-9 w-full rounded-lg border border-border bg-background px-2.5 text-sm text-foreground disabled:cursor-not-allowed disabled:opacity-55"
        disabled={disabled}
        onChange={event => onChange(event.currentTarget.value)}
        value={value}
      >
        {options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </label>
  )
}

interface RowProps {
  baseline: HermesUiDailyPlanningDraftRow
  draft: HermesUiDailyPlanningDraftRow
  expanded: boolean
  isRtl: boolean
  onPatch: (patch: Partial<HermesUiDailyPlanningDraftRow>) => void
  onReset: () => void
  onToggleExpanded: () => void
  row: HermesUiDailyPlanningRow
  suggestion: boolean
}

function DailyPlanningRow({ baseline, draft, expanded, isRtl, onPatch, onReset, onToggleExpanded, row, suggestion }: RowProps) {
  const dirty = !same(baseline, draft)
  const sourceMutable = row.sourceMutationAllowed && row.source.kind !== 'calendar'
  const contextId = draft.context[0]?.id ?? `context-${row.id}`
  const directionStyle = { direction: isRtl ? 'rtl' : 'ltr', textAlign: isRtl ? 'right' : 'left' } satisfies CSSProperties

  const runQuickAction = (id: string) => {
    if (id === 'mark-done' && sourceMutable) {onPatch({ sourceStatus: 'done' })}

    if (id === 'start' && sourceMutable) {onPatch({ sourceStatus: 'in-progress' })}

    if (id === 'defer') {onPatch({ planPlacement: 'not-today' })}

    if (id === 'add-to-plan') {onPatch({ suggestionSelected: true })}

    if (id === 'remove-from-plan') {onPatch({ suggestionSelected: false })}
  }

  const toggleClaim = (claimId: string, checked: boolean) => {
    const claim = row.learnedClaims?.find(candidate => candidate.id === claimId)

    if (!claim) {return}
    onPatch({
      proposedLearningClaims: checked
        ? [...draft.proposedLearningClaims.filter(candidate => candidate.id !== claimId), claim]
        : draft.proposedLearningClaims.filter(candidate => candidate.id !== claimId)
    })
  }

  const toggleGeneralization = (proposalId: string, checked: boolean) => {
    onPatch({
      approvedGeneralizationIds: checked
        ? [...new Set([...draft.approvedGeneralizationIds, proposalId])]
        : draft.approvedGeneralizationIds.filter(id => id !== proposalId)
    })
  }

  return (
    <article
      className={cn('space-y-3 px-4 py-4', dirty && 'bg-amber-500/5')}
      data-testid={`daily-planning-row-${row.id}`}
      dir={isRtl ? 'rtl' : 'ltr'}
      style={directionStyle}
    >
      <div className="flex items-start gap-2">
        <button
          aria-expanded={expanded}
          aria-label={text(isRtl, `${expanded ? 'כווץ' : 'הרחב'} ${row.title}`, `${expanded ? 'Collapse' : 'Expand'} ${row.title}`)}
          className="mt-0.5 size-8 shrink-0 rounded-lg border border-border text-base text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          onClick={onToggleExpanded}
          type="button"
        >
          {expanded ? '−' : '+'}
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              <h4 className="m-0 text-base font-semibold leading-6 text-foreground wrap-anywhere">{row.title}</h4>
              <div className="mt-1 text-sm leading-5 text-muted-foreground">
                {row.temporal?.startTime ? `${row.temporal.startTime}${row.temporal.endTime ? `–${row.temporal.endTime}` : ''} · ` : ''}
                {row.suggestionRationale || row.context?.[0]?.text || row.nextStep || ''}
              </div>
            </div>
            <div className="flex items-center gap-1.5">
              {dirty && <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[0.62rem] text-amber-700 dark:text-amber-300">{text(isRtl, 'שונה', 'Changed')}</span>}
              {dirty && (
                <button
                  aria-label={text(isRtl, `בטל שינויים ב${row.title}`, `Undo changes to ${row.title}`)}
                  className="rounded border border-border/60 px-1.5 py-0.5 text-[0.62rem] text-muted-foreground hover:bg-muted"
                  onClick={onReset}
                  type="button"
                >
                  {text(isRtl, 'בטל', 'Undo')}
                </button>
              )}
            </div>
          </div>
        </div>
      </div>

      {suggestion && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-muted/45 px-3 py-2.5 text-sm">
          {row.suggestionConfidence && row.expectedImpact && (
            <div className="leading-5 text-muted-foreground">{row.expectedImpact}</div>
          )}
          <div className="flex items-center gap-2">
            <button
              aria-label={text(
                isRtl,
                `${draft.suggestionSelected ? 'הסר את' : 'הוסף את'} ${row.title} ${draft.suggestionSelected ? 'מהתכנית' : 'לתכנית'}`,
                `${draft.suggestionSelected ? 'Remove' : 'Add'} ${row.title} ${draft.suggestionSelected ? 'from' : 'to'} the plan`
              )}
              className="rounded-lg border border-border bg-background px-3 py-1.5 font-medium text-foreground transition-colors hover:bg-muted"
              onClick={() => onPatch({ suggestionSelected: !draft.suggestionSelected })}
              type="button"
            >
              {draft.suggestionSelected ? text(isRtl, 'הסר', 'Remove') : text(isRtl, 'הוסף', 'Add')}
            </button>
          </div>
        </div>
      )}

      {expanded && <div className="space-y-4 border-t border-border/60 pt-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <SelectField
          disabled={!sourceMutable}
          label={text(isRtl, `סטטוס מקור — ${row.title}`, `Source status — ${row.title}`)}
          onChange={value => onPatch({ sourceStatus: value as HermesUiDailyPlanningDraftRow['sourceStatus'] })}
          options={[
            { label: text(isRtl, 'פתוח', 'Open'), value: 'open' },
            { label: text(isRtl, 'בתהליך', 'In progress'), value: 'in-progress' },
            { label: text(isRtl, 'הושלם', 'Done'), value: 'done' },
            { label: text(isRtl, 'בוטל', 'Cancelled'), value: 'cancelled' },
            { label: text(isRtl, 'לא ידוע', 'Unknown'), value: 'unknown' }
          ]}
          value={draft.sourceStatus}
        />
        <SelectField
          label={text(isRtl, `מיקום היום — ${row.title}`, `Today placement — ${row.title}`)}
          onChange={value => onPatch({ planPlacement: value as HermesUiDailyPlanningDraftRow['planPlacement'] })}
          options={[
            { label: text(isRtl, 'לא שובץ', 'Unassigned'), value: 'unassigned' },
            { label: text(isRtl, 'ליבה', 'Core'), value: 'core' },
            { label: text(isRtl, 'אופציונלי', 'Optional'), value: 'optional' },
            { label: text(isRtl, 'לא היום', 'Not today'), value: 'not-today' }
          ]}
          value={draft.planPlacement}
        />
        <SelectField
          label={text(isRtl, `ודאות הקשר — ${row.title}`, `Context confidence — ${row.title}`)}
          onChange={value => onPatch({ contextConfidence: value as HermesUiDailyPlanningDraftRow['contextConfidence'] })}
          options={[
            { label: text(isRtl, 'מאומת', 'Verified'), value: 'verified' },
            { label: text(isRtl, 'חלקי', 'Partial'), value: 'partial' },
            { label: text(isRtl, 'לא ידוע', 'Unknown'), value: 'unknown' },
            { label: text(isRtl, 'סתירה', 'Conflict'), value: 'conflict' }
          ]}
          value={draft.contextConfidence}
        />
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <SelectField
          disabled={!sourceMutable}
          label={text(isRtl, `עדיפות — ${row.title}`, `Priority — ${row.title}`)}
          onChange={value => onPatch({ priority: value ? value as NonNullable<HermesUiDailyPlanningDraftRow['priority']> : null })}
          options={[
            { label: text(isRtl, 'ללא', 'None'), value: '' },
            { label: text(isRtl, 'גבוהה', 'High'), value: 'high' },
            { label: text(isRtl, 'בינונית', 'Medium'), value: 'medium' },
            { label: text(isRtl, 'נמוכה', 'Low'), value: 'low' }
          ]}
          value={draft.priority ?? ''}
        />
        <label className="min-w-32 flex-1 text-xs font-medium text-muted-foreground">
          <span className="mb-1 block">{text(isRtl, 'תאריך יעד', 'Due date')}</span>
          <input
            aria-label={text(isRtl, `תאריך יעד — ${row.title}`, `Due date — ${row.title}`)}
            className="h-9 w-full rounded-lg border border-border bg-background px-2.5 text-sm text-foreground disabled:opacity-55"
            disabled={!sourceMutable}
            onChange={event => onPatch({ dueDate: event.currentTarget.value || null })}
            type="date"
            value={draft.dueDate ?? ''}
          />
        </label>
        <label className="min-w-32 flex-1 text-xs font-medium text-muted-foreground">
          <span className="mb-1 block">{text(isRtl, 'משך בדקות', 'Duration in minutes')}</span>
          <input
            aria-label={text(isRtl, `משך — ${row.title}`, `Duration — ${row.title}`)}
            className="h-9 w-full rounded-lg border border-border bg-background px-2.5 text-sm text-foreground"
            min={1}
            onChange={event => onPatch({ durationMinutes: event.currentTarget.value ? Number(event.currentTarget.value) : undefined })}
            type="number"
            value={draft.durationMinutes ?? ''}
          />
        </label>
        <SelectField
          label={text(isRtl, `אנרגיה — ${row.title}`, `Energy — ${row.title}`)}
          onChange={value => onPatch({ energy: value ? value as NonNullable<HermesUiDailyPlanningDraftRow['energy']> : undefined })}
          options={[
            { label: text(isRtl, 'לא הוגדר', 'Unset'), value: '' },
            { label: text(isRtl, 'נמוכה', 'Low'), value: 'low' },
            { label: text(isRtl, 'בינונית', 'Medium'), value: 'medium' },
            { label: text(isRtl, 'גבוהה', 'High'), value: 'high' },
            { label: text(isRtl, 'לא ידוע', 'Unknown'), value: 'unknown' }
          ]}
          value={draft.energy ?? ''}
        />
      </div>

      {row.quickActions && row.quickActions.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {row.quickActions.map(action => (
            <button
              className="rounded-lg border border-border px-3 py-1.5 text-xs text-muted-foreground disabled:opacity-50"
              disabled={action.id === 'open-source'}
              key={action.id}
              onClick={() => runQuickAction(action.id)}
              type="button"
            >
              {action.label}
            </button>
          ))}
        </div>
      )}

        <div className="space-y-3 rounded-lg bg-muted/30 p-3">
          <div className="text-xs text-muted-foreground">{row.source.kind} · <bdi>{row.source.recordId}</bdi></div>
          <label className="block text-xs font-medium text-muted-foreground">
            {text(isRtl, 'הקשר מלא', 'Full context')}
            <textarea
              aria-label={text(isRtl, `הקשר — ${row.title}`, `Context — ${row.title}`)}
              className="mt-1 min-h-20 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground"
              onChange={event => onPatch({ context: [{ id: contextId, text: event.currentTarget.value }] })}
              value={rowContextValue(draft)}
            />
          </label>
          <div className="grid gap-2 sm:grid-cols-2">
            <label className="block text-[0.68rem] font-medium text-muted-foreground">
              {text(isRtl, 'הצעד הבא', 'Next step')}
              <textarea
                aria-label={text(isRtl, `הצעד הבא — ${row.title}`, `Next step — ${row.title}`)}
                className="mt-1 min-h-14 w-full rounded-md border border-border/65 bg-background px-2 py-1.5 text-[0.72rem] text-foreground"
                onChange={event => onPatch({ nextStep: event.currentTarget.value || undefined })}
                value={draft.nextStep ?? ''}
              />
            </label>
            <label className="block text-[0.68rem] font-medium text-muted-foreground">
              {text(isRtl, 'מספיק לסיום', 'Done enough')}
              <textarea
                aria-label={text(isRtl, `מספיק לסיום — ${row.title}`, `Done enough — ${row.title}`)}
                className="mt-1 min-h-14 w-full rounded-md border border-border/65 bg-background px-2 py-1.5 text-[0.72rem] text-foreground"
                onChange={event => onPatch({ doneEnough: event.currentTarget.value || undefined })}
                value={draft.doneEnough ?? ''}
              />
            </label>
          </div>

          {row.provenance && row.provenance.length > 0 && (
            <div>
              <div className="text-[0.66rem] font-semibold text-muted-foreground">{text(isRtl, 'מקור וזמן קליטה', 'Source provenance and capture time')}</div>
              {row.provenance.map(entry => (
                <div className="mt-1 text-[0.66rem] text-muted-foreground" key={entry.id}>{entry.text || entry.sourceKind} · {entry.capturedAt}</div>
              ))}
            </div>
          )}

          {row.learnedClaims && row.learnedClaims.length > 0 && (
            <div className="space-y-1.5">
              <div className="text-[0.66rem] font-semibold text-muted-foreground">{text(isRtl, 'טענות שנלמדו', 'Learned claims')}</div>
              {row.learnedClaims.map(claim => (
                <div className="rounded border border-border/55 px-2 py-1.5" key={claim.id}>
                  <div className="text-[0.68rem] text-foreground">{claim.text}</div>
                  <div className="text-[0.62rem] text-muted-foreground">{claim.state} · {claim.scope.kind}{claim.scope.referenceId ? ` · ${claim.scope.referenceId}` : ''}</div>
                  <label className="mt-1 flex items-center gap-1.5 text-[0.64rem] text-muted-foreground">
                    <input
                      aria-label={text(isRtl, `הצע את ${claim.id} כלמידה מתמשכת`, `Propose ${claim.id} as durable learning`)}
                      checked={draft.proposedLearningClaims.some(candidate => candidate.id === claim.id)}
                      onChange={event => toggleClaim(claim.id, event.currentTarget.checked)}
                      type="checkbox"
                    />
                    {text(isRtl, 'כלול כשינוי למידה', 'Include as a learning change')}
                  </label>
                </div>
              ))}
            </div>
          )}

          {row.learningConflicts?.map(conflict => (
            <fieldset className="rounded border border-amber-500/35 bg-amber-500/5 p-2" key={conflict.id}>
              <legend className="px-1 text-[0.66rem] font-semibold text-amber-700 dark:text-amber-300">
                {text(isRtl, 'סתירת למידה', 'Learning conflict')}
              </legend>
              <div className="text-[0.64rem] text-muted-foreground">{conflict.priorClaimId} ↔ {conflict.newClaimId}</div>
              {([
                ['keep-prior', text(isRtl, 'השאר את הטענה הקודמת', 'Keep the prior claim')],
                ['activate-new', text(isRtl, 'הפעל את הטענה החדשה', 'Activate the new claim')],
                ['keep-both', text(isRtl, 'השאר את שתיהן פעילות בהקשרים נפרדים', 'Keep both for separate contexts')]
              ] as Array<[HermesUiDailyPlanningConflictDecision, string]>).map(([decision, decisionLabel]) => (
                <label className="mt-1 flex items-center gap-1.5 text-[0.64rem]" key={decision}>
                  <input
                    aria-label={decisionLabel}
                    checked={draft.conflictResolutions[conflict.id] === decision}
                    name={`${row.id}-${conflict.id}`}
                    onChange={() => onPatch({ conflictResolutions: { ...draft.conflictResolutions, [conflict.id]: decision } })}
                    type="radio"
                  />
                  {decisionLabel}
                </label>
              ))}
            </fieldset>
          ))}

          {row.generalizationProposals?.map(proposal => (
            <label className="flex items-start gap-2 rounded border border-border/55 px-2 py-1.5 text-[0.66rem]" key={proposal.id}>
              <input
                aria-label={text(isRtl, `החל על ${proposal.scope.referenceId || proposal.scope.kind}`, `Apply to ${proposal.scope.referenceId || proposal.scope.kind}`)}
                checked={draft.approvedGeneralizationIds.includes(proposal.id)}
                className="mt-0.5"
                onChange={event => toggleGeneralization(proposal.id, event.currentTarget.checked)}
                type="checkbox"
              />
              <span><strong>{proposal.scope.kind}</strong> · {proposal.rationale}</span>
            </label>
          ))}
        </div>
      </div>}
    </article>
  )
}

function VirtualSectionRows({ children, rows }: { children: (row: HermesUiDailyPlanningRow) => ReactNode; rows: HermesUiDailyPlanningRow[] }) {
  const scrollerRef = useRef<HTMLDivElement | null>(null)

  const virtualizer = useVirtualizer({
    count: rows.length,
    estimateSize: () => 160,
    getItemKey: index => rows[index]?.id ?? index,
    getScrollElement: () => scrollerRef.current,
    initialRect: { height: 520, width: 800 },
    overscan: 8
  })

  return (
    <div className="max-h-[32rem] overflow-y-auto" ref={scrollerRef}>
      <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
        {virtualizer.getVirtualItems().map(item => {
          const row = rows[item.index]

          if (!row) {return null}

          return (
            <div
              className="absolute left-0 top-0 w-full border-b border-border/45"
              data-index={item.index}
              key={row.id}
              ref={virtualizer.measureElement}
              style={{ transform: `translateY(${item.start}px)` }}
            >
              {children(row)}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function SectionRows({ children, rows }: { children: (row: HermesUiDailyPlanningRow) => ReactNode; rows: HermesUiDailyPlanningRow[] }) {
  if (rows.length < VIRTUALIZE_AT) {return <div className="divide-y divide-border/45">{rows.map(row => <div key={row.id}>{children(row)}</div>)}</div>}

  return <VirtualSectionRows rows={rows}>{children}</VirtualSectionRows>
}

function ReviewGroup({ children, title }: { children: ReactNode; title: string }) {
  return (
    <section className="rounded-md border border-border/60 bg-background/35 p-2">
      <h4 className="text-[0.72rem] font-semibold text-foreground">{title}</h4>
      <div className="mt-1 space-y-1 text-[0.66rem] text-muted-foreground">{children}</div>
    </section>
  )
}

function DiffRows({ changes }: { changes: Array<{ changes: Record<string, { after: unknown; before: unknown }>; rowId: string }> }) {
  if (changes.length === 0) {return <div>—</div>}

  return changes.flatMap(change => Object.entries(change.changes).map(([field, diff]) => (
    <div key={`${change.rowId}:${field}`}><strong>{change.rowId} · {field}</strong>: <span>{formatDiffValue(diff.before)} → {formatDiffValue(diff.after)}</span></div>
  )))
}

function formatDiffValue(value: unknown): string {
  if (Array.isArray(value)) {
    if (value.length === 1 && typeof value[0] === 'object' && value[0] && 'text' in value[0]) {return String((value[0] as { text: unknown }).text)}

    return JSON.stringify(value)
  }

  if (value === undefined || value === null || value === '') {return '—'}

  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

export function DailyPlanningListCard({ artifact }: { artifact: HermesUiDailyPlanningListArtifact }) {
  const latest = useLatestPlanningCard(`${artifact.id}:${artifact.baselineId}`)
  const isRtl = artifact.direction === 'rtl' || (artifact.direction !== 'ltr' && /[\u0590-\u05ff]/.test(`${artifact.title}\n${artifact.description || ''}`))
  const baseline = useMemo(() => createHermesUiDailyPlanningDraft(artifact), [artifact])
  const compactPlan = artifact.sections.reduce((count, section) => count + section.rows.length, 0) >= COLLAPSE_PLAN_AT
  const [draft, setDraft] = useState<HermesUiDailyPlanningDraft>(() => structuredClone(baseline))
  const [expandedRows, setExpandedRows] = useState<Set<string>>(() => new Set())
  const [openSections, setOpenSections] = useState<Set<string>>(() => new Set())
  const [moreSuggestionsOpen, setMoreSuggestionsOpen] = useState(false)
  const [reviewOpen, setReviewOpen] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const dirtyRows = Object.keys(draft.rows).filter(id => !same(draft.rows[id], baseline.rows[id]))

  const patchRow = (rowId: string, patch: Partial<HermesUiDailyPlanningDraftRow>) => {
    setDraft(current => ({ rows: { ...current.rows, [rowId]: { ...current.rows[rowId], ...patch } as HermesUiDailyPlanningDraftRow } }))
    setSubmitted(false)
  }

  const resetRow = (rowId: string) => {
    setDraft(current => ({ rows: { ...current.rows, [rowId]: structuredClone(baseline.rows[rowId]) } }))
    setSubmitted(false)
  }

  const resetAll = () => {
    setDraft(structuredClone(baseline))
    setSubmitted(false)
  }

  const unresolvedConflicts = artifact.sections.flatMap(section => section.rows).flatMap(row => {
    const current = draft.rows[row.id]

    if (!current?.proposedLearningClaims.length) {return []}

    return (row.learningConflicts ?? []).filter(conflict => !current.conflictResolutions[conflict.id])
  })

  let response: ReturnType<typeof buildHermesUiDailyPlanningListResponse> | null = null

  if (unresolvedConflicts.length === 0) {
    try {
      response = buildHermesUiDailyPlanningListResponse(artifact, draft)
    } catch {
      response = null
    }
  }

  const submit = () => {
    if (submitted || !response || unresolvedConflicts.length > 0) {return}
    setSubmitted(true)
    requestComposerSubmit(`Hermes UI daily planning response:\n${JSON.stringify(response)}`, {
      allowWhileBusy: true,
      hidden: true,
      target: 'main'
    })
  }

  if (!latest) {return null}

  return (
    <section
      aria-label={artifact.title}
      className="my-4 overflow-hidden rounded-2xl border border-border/70 bg-background/75 shadow-sm"
      data-hermes-ui-artifact="daily-planning-list"
      dir={isRtl ? 'rtl' : 'ltr'}
    >
      <header className="border-b border-border/60 px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h3 className="m-0 text-lg font-semibold leading-7 text-foreground">{artifact.title}</h3>
            {artifact.description && <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">{artifact.description}</p>}
            <div className="mt-2 text-xs text-muted-foreground">{artifact.date}</div>
          </div>
          {dirtyRows.length > 0 && <span className="rounded-full bg-amber-500/15 px-3 py-1 text-xs text-amber-700 dark:text-amber-300">
            {text(isRtl, `${dirtyRows.length} שינויים`, `${dirtyRows.length} changes`)}
          </span>}
        </div>
      </header>

      <div className="divide-y divide-border/55 px-5">
        {artifact.sections.map(section => {
          if (section.rows.length === 0) {return null}

          if (section.kind === 'more-suggestions' && !moreSuggestionsOpen) {
            return (
              <button
                aria-expanded={false}
                className="my-3 w-full rounded-lg border border-border px-3 py-2 text-sm font-medium text-muted-foreground"
                key={section.id}
                onClick={() => setMoreSuggestionsOpen(true)}
                type="button"
              >
                {text(isRtl, `הצג ${section.rows.length} הצעות נוספות`, `Show ${section.rows.length} more suggestions`)}
              </button>
            )
          }

          const rows = visibleRows(section)
          const suggestion = section.kind === 'suggestions' || section.kind === 'more-suggestions'
          const sectionOpen = !compactPlan || openSections.has(section.id)

          return (
            <section className="py-3" data-testid={`daily-planning-section-${section.kind}`} key={section.id}>
              <button
                aria-expanded={sectionOpen}
                className="flex w-full items-center justify-between px-1 py-2 text-start"
                onClick={() => compactPlan && setOpenSections(current => {
                  const next = new Set(current)

                  if (next.has(section.id)) {next.delete(section.id)}
                  else {next.add(section.id)}

                  return next
                })}
                type="button"
              >
                <h3 className="text-sm font-semibold text-foreground">{section.title}</h3>
                <div className="flex items-center gap-2">
                  <span className="text-xs tabular-nums text-muted-foreground">{rows.length}</span>
                  {compactPlan && <span className="text-muted-foreground">{sectionOpen ? '−' : '+'}</span>}
                </div>
              </button>
              {sectionOpen && <SectionRows rows={rows}>
                {row => (
                  <DailyPlanningRow
                    baseline={baseline.rows[row.id]}
                    draft={draft.rows[row.id]}
                    expanded={expandedRows.has(row.id)}
                    isRtl={isRtl}
                    onPatch={patch => patchRow(row.id, patch)}
                    onReset={() => resetRow(row.id)}
                    onToggleExpanded={() => setExpandedRows(current => {
                      const next = new Set(current)

                      if (next.has(row.id)) {next.delete(row.id)}
                      else {next.add(row.id)}

                      return next
                    })}
                    row={row}
                    suggestion={suggestion}
                  />
                )}
              </SectionRows>}
            </section>
          )
        })}
      </div>

      {dirtyRows.length > 0 && <footer className="border-t border-border/65 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <button className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground" onClick={() => setReviewOpen(true)} type="button">
            {text(isRtl, 'סקירת שינויים', 'Review changes')}
          </button>
          <button
            aria-label={text(isRtl, 'אפס את כל השינויים', 'Reset all changes')}
            className="rounded-md border border-border/65 px-3 py-1.5 text-[0.72rem] text-muted-foreground disabled:opacity-50"
            disabled={dirtyRows.length === 0}
            onClick={resetAll}
            type="button"
          >
            {text(isRtl, 'אפס הכול', 'Reset all')}
          </button>
        </div>

        {reviewOpen && (
          <div aria-label={text(isRtl, 'סקירת שינויים מאוחדת', 'Consolidated change review')} className="mt-3 space-y-2 rounded-lg border border-border/65 bg-muted/20 p-2" role="region">
            <ReviewGroup title={text(isRtl, 'שינויים במערכות המקור', 'Source-system changes')}>
              <DiffRows changes={(response?.sourceChanges ?? []) as Array<{ changes: Record<string, { after: unknown; before: unknown }>; rowId: string }>} />
            </ReviewGroup>
            <ReviewGroup title={text(isRtl, 'שינויים בתכנית היום', "Today's plan changes")}>
              <DiffRows changes={(response?.dayPlanChanges ?? []) as Array<{ changes: Record<string, { after: unknown; before: unknown }>; rowId: string }>} />
            </ReviewGroup>
            <ReviewGroup title={text(isRtl, 'שינויים בלמידה מתמשכת', 'Durable-learning changes')}>
              {artifact.sections.flatMap(section => section.rows).flatMap(row => draft.rows[row.id]?.proposedLearningClaims ?? []).map(claim => <div key={claim.id}>{claim.text}</div>)}
              {artifact.sections.flatMap(section => section.rows).every(row => (draft.rows[row.id]?.proposedLearningClaims.length ?? 0) === 0) && <div>—</div>}
            </ReviewGroup>
            {unresolvedConflicts.length > 0 && (
              <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[0.68rem] text-amber-800 dark:text-amber-200">
                {text(isRtl, 'נדרשת הכרעה בין טענה קודמת לחדשה', 'A prior/new claim decision is required')}
              </div>
            )}
            <button
              className="rounded-md bg-primary px-3 py-1.5 text-[0.72rem] font-medium text-primary-foreground disabled:opacity-50"
              disabled={submitted || unresolvedConflicts.length > 0 || !response}
              onClick={submit}
              type="button"
            >
              {text(isRtl, 'שלח בקשת preview ל־Hermes', 'Send preview request to Hermes')}
            </button>
          </div>
        )}
      </footer>}
    </section>
  )
}
