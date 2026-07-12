import { useStore } from '@nanostores/react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { cn } from '@/lib/utils'
import {
  $personalAssistantState,
  type AssistantStateItem,
  type AssistantStateSection,
  patchPersonalAssistantState
} from '@/store/personal-assistant'

const itemLabel = (item: AssistantStateItem) => item.title || item.summary || item.id

function SituationItem({ item, section }: { item: AssistantStateItem; section: AssistantStateSection }) {
  const [expanded, setExpanded] = useState(false)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(itemLabel(item))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const mutate = async (op: 'archive' | 'forget' | 'upsert') => {
    setBusy(true)
    setError(null)

    try {
      await patchPersonalAssistantState([
        {
          id: item.id,
          op,
          section,
          ...(op === 'upsert' ? { value: { ...item, title: draft.trim() } } : {})
        }
      ])
      setEditing(false)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The change could not be saved.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="rounded-md border border-(--ui-stroke-tertiary) bg-(--ui-control-background) px-2 py-1.5">
      <div className="flex min-w-0 items-center gap-1.5">
        {editing ? (
          <input
            aria-label={`Edit ${itemLabel(item)}`}
            autoFocus
            className="min-w-0 flex-1 rounded border border-(--ui-stroke-secondary) bg-transparent px-1.5 py-1 text-xs outline-none focus:border-primary"
            onChange={event => setDraft(event.target.value)}
            value={draft}
          />
        ) : (
          <span className="min-w-0 flex-1 truncate text-xs text-(--ui-text-primary)">{itemLabel(item)}</span>
        )}
        {editing ? (
          <Button disabled={busy || !draft.trim()} onClick={() => void mutate('upsert')} size="xs" type="button">
            Save
          </Button>
        ) : (
          <>
            <Button aria-label={`Inspect ${itemLabel(item)}`} onClick={() => setExpanded(value => !value)} size="icon-xs" type="button" variant="ghost">
              <Codicon name="info" />
            </Button>
            <Button aria-label={`Edit ${itemLabel(item)}`} onClick={() => setEditing(true)} size="icon-xs" type="button" variant="ghost">
              <Codicon name="edit" />
            </Button>
            <Button aria-label={`Archive ${itemLabel(item)}`} disabled={busy} onClick={() => void mutate('archive')} size="icon-xs" type="button" variant="ghost">
              <Codicon name="archive" />
            </Button>
            <Button aria-label={`Forget ${itemLabel(item)}`} disabled={busy} onClick={() => void mutate('forget')} size="icon-xs" type="button" variant="ghost">
              <Codicon name="trash" />
            </Button>
          </>
        )}
      </div>
      {expanded && (
        <pre className="mt-1.5 max-h-28 overflow-auto whitespace-pre-wrap break-words text-[0.6875rem] text-(--ui-text-tertiary)">
          {JSON.stringify(item, null, 2)}
        </pre>
      )}
      {error && <p className="mt-1 text-[0.6875rem] text-destructive" role="alert">{error}</p>}
    </li>
  )
}

function SituationSection({
  items,
  section,
  title
}: {
  items: AssistantStateItem[]
  section: AssistantStateSection
  title: string
}) {
  return (
    <section aria-label={title} className="min-w-0">
      <h3 className="mb-1 text-[0.6875rem] font-semibold uppercase tracking-wide text-(--ui-text-tertiary)">{title}</h3>
      {items.length ? (
        <ul className="space-y-1">
          {items.map(item => <SituationItem item={item} key={item.id} section={section} />)}
        </ul>
      ) : (
        <p className="text-xs text-(--ui-text-tertiary)">None</p>
      )}
    </section>
  )
}

export function PersonalAssistantSituation() {
  const state = useStore($personalAssistantState)
  const [open, setOpen] = useState(true)

  if (!state) {
    return null
  }

  const pending = state.pendingApprovals.length + state.captureProposals.length

  return (
    <aside className="relative z-10 shrink-0 border-b border-(--ui-stroke-tertiary) bg-(--ui-sidebar-surface-background)" dir="auto">
      <button
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-start hover:bg-(--ui-control-hover-background)"
        onClick={() => setOpen(value => !value)}
        type="button"
      >
        <Codicon name="sparkle" />
        <span className="flex-1 text-xs font-semibold">Situation</span>
        <span className={cn('text-[0.6875rem]', state.sync.status === 'fresh' ? 'text-success' : 'text-warning')}>
          {state.sync.status}
        </span>
        {pending > 0 && <span className="rounded-full bg-primary px-1.5 text-[0.6875rem] text-primary-foreground">{pending}</span>}
        <Codicon name={open ? 'chevron-up' : 'chevron-down'} />
      </button>
      {open && (
        <div className="grid max-h-[min(42vh,24rem)] grid-cols-1 gap-3 overflow-auto px-3 pb-3 sm:grid-cols-2 xl:grid-cols-3">
          <SituationSection items={state.outcomes} section="outcomes" title="Outcomes" />
          <SituationSection items={state.commitments} section="commitments" title="Commitments" />
          <section aria-label="Capacity and focus">
            <h3 className="mb-1 text-[0.6875rem] font-semibold uppercase tracking-wide text-(--ui-text-tertiary)">Capacity & focus</h3>
            <p className="text-xs">{state.capacity.summary || 'Not set'}</p>
            {state.focus && <p className="mt-1 text-xs font-medium">Focus: {itemLabel(state.focus)}</p>}
          </section>
          <SituationSection items={state.blockers} section="blockers" title="Blockers" />
          <SituationSection items={state.deferred} section="deferred" title="Deferred important work" />
          <SituationSection items={state.preferences ?? []} section="preferences" title="Preferences" />
          <section aria-label="Pending approvals and proposals">
            <h3 className="mb-1 text-[0.6875rem] font-semibold uppercase tracking-wide text-(--ui-text-tertiary)">Pending</h3>
            <p className="text-xs">{state.pendingApprovals.length} approvals · {state.captureProposals.length} proposals</p>
            {[...state.pendingApprovals, ...state.captureProposals].length > 0 && (
              <ul className="mt-1 space-y-1 text-xs">
                {[...state.pendingApprovals, ...state.captureProposals].map(item => (
                  <li className="rounded border border-(--ui-stroke-tertiary) px-1.5 py-1" key={item.id}>
                    {itemLabel(item)}
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-1 text-[0.6875rem] text-(--ui-text-tertiary)">Last verified: {state.sync.lastVerifiedAt || 'never'}</p>
          </section>
        </div>
      )}
    </aside>
  )
}
