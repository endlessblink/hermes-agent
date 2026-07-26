import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import {
  $personalAssistantState,
  acknowledgePersonalAssistantRead,
  type AssistantPendingItem,
  type AssistantState,
  type AssistantStateItem,
  type AssistantStateSection,
  patchPersonalAssistantState,
  refreshPersonalAssistantState
} from '@/store/personal-assistant'
import { $threadScrolledUp } from '@/store/thread-scroll'

const itemLabel = (item: AssistantStateItem) => item.title || item.summary || item.id

const learningSection = (item: AssistantPendingItem): 'commitments' | 'outcomes' | 'preferences' | null => {
  const section = item.section

  return section === 'commitments' || section === 'outcomes' || section === 'preferences' ? section : null
}

function CaptureProposalItem({ item }: { item: AssistantPendingItem }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(itemLabel(item))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const section = learningSection(item)
  const singular = section === 'commitments' ? 'commitment' : section === 'outcomes' ? 'outcome' : 'preference'

  const review = async (status: 'accepted' | 'rejected') => {
    if (status === 'accepted' && (!section || !draft.trim())) {
      setError('This proposal is missing a valid learning category or title.')

      return
    }

    setBusy(true)
    setError(null)

    try {
      await patchPersonalAssistantState(
        status === 'accepted' && section
          ? [
              {
                id: item.id,
                op: 'upsert',
                section,
                value: { ...item, status: 'accepted', title: draft.trim() }
              },
              {
                id: item.id,
                op: 'upsert',
                section: 'captureProposals',
                value: { ...item, status: 'accepted', title: draft.trim() }
              }
            ]
          : [
              {
                id: item.id,
                op: 'upsert',
                section: 'captureProposals',
                value: { ...item, status: 'rejected', title: draft.trim() }
              }
            ]
      )
      setEditing(false)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The proposal could not be reviewed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <li
      className="rounded-lg border border-(--ui-stroke-secondary) bg-(--ui-chat-surface-background) p-4 text-start"
      dir="auto"
    >
      {editing ? (
        <input
          aria-label={`Edit ${itemLabel(item)}`}
          autoFocus
          className="w-full rounded-md border border-(--ui-stroke-secondary) bg-transparent px-3 py-2 text-sm outline-none focus:border-primary"
          onChange={event => setDraft(event.target.value)}
          value={draft}
        />
      ) : (
        <span className="block text-sm leading-relaxed text-(--ui-text-primary)">{draft}</span>
      )}
      <div className="mt-4 flex flex-wrap gap-2 border-t border-(--ui-stroke-tertiary) pt-3">
        <Button
          aria-label={`Accept learned ${singular}`}
          disabled={busy || !section || !draft.trim()}
          onClick={() => void review('accepted')}
          size="sm"
          type="button"
        >
          Accept
        </Button>
        <Button
          aria-label={`Edit ${itemLabel(item)}`}
          disabled={busy}
          onClick={() => setEditing(value => !value)}
          size="sm"
          type="button"
          variant="ghost"
        >
          Edit
        </Button>
        <Button
          aria-label={`Reject learned ${singular}`}
          disabled={busy}
          onClick={() => void review('rejected')}
          size="sm"
          type="button"
          variant="ghost"
        >
          Reject
        </Button>
      </div>
      {error && <p className="mt-1 text-[0.6875rem] text-destructive">{error}</p>}
    </li>
  )
}

function SituationItem({ item, section }: { item: AssistantStateItem; section: AssistantStateSection }) {
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
            className="min-w-0 flex-1 rounded border border-(--ui-stroke-secondary) bg-transparent px-1.5 py-1 text-start text-xs outline-none focus:border-primary"
            dir="auto"
            onChange={event => setDraft(event.target.value)}
            value={draft}
          />
        ) : (
          <span className="min-w-0 flex-1 truncate text-start text-xs text-(--ui-text-primary)" dir="auto">
            {itemLabel(item)}
          </span>
        )}
        {editing ? (
          <Button disabled={busy || !draft.trim()} onClick={() => void mutate('upsert')} size="xs" type="button">
            Save
          </Button>
        ) : (
          <>
            <Button
              aria-label={`Edit ${itemLabel(item)}`}
              onClick={() => setEditing(true)}
              size="icon-xs"
              type="button"
              variant="ghost"
            >
              <Codicon name="edit" />
            </Button>
            <Button
              aria-label={`Archive ${itemLabel(item)}`}
              disabled={busy}
              onClick={() => void mutate('archive')}
              size="icon-xs"
              type="button"
              variant="ghost"
            >
              <Codicon name="archive" />
            </Button>
            <Button
              aria-label={`Forget ${itemLabel(item)}`}
              disabled={busy}
              onClick={() => void mutate('forget')}
              size="icon-xs"
              type="button"
              variant="ghost"
            >
              <Codicon name="trash" />
            </Button>
          </>
        )}
      </div>
      {error && (
        <p className="mt-1 text-start text-[0.6875rem] text-destructive" dir="auto" role="alert">
          {error}
        </p>
      )}
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
  if (!items.length) {
    return null
  }

  return (
    <section aria-label={title} className="min-w-0">
      <h3 className="mb-1 text-[0.6875rem] font-semibold uppercase tracking-wide text-(--ui-text-tertiary)">{title}</h3>
      <ul className="space-y-1">
        {items.map(item => (
          <SituationItem item={item} key={item.id} section={section} />
        ))}
      </ul>
    </section>
  )
}

export type PersonalAssistantAttention = {
  blockers: number
  decisions: number
  safety: number
  total: number
}

export function getPersonalAssistantAttention(state: AssistantState | null): PersonalAssistantAttention {
  if (!state) {
    return { blockers: 0, decisions: 0, safety: 0, total: 0 }
  }

  const pendingApprovals = state.pendingApprovals.filter(item => !item.status || item.status === 'pending').length
  const captureProposals = state.captureProposals.filter(item => !item.status || item.status === 'pending').length
  const receipt = state.latestCoverageReceipt

  const receiptIssues = receipt
    ? new Set([...receipt.missingItemIds, ...receipt.riskItemIds, ...receipt.unresolvedItemIds]).size
    : 0

  const safety = receipt ? Math.max(receiptIssues, receipt.complete ? 0 : 1) : (state.protectedItems ?? []).length

  const decisions = pendingApprovals + captureProposals
  const blockers = state.blockers.length

  return { blockers, decisions, safety, total: blockers + decisions + safety }
}

function attentionHeadline(attention: PersonalAssistantAttention) {
  if (attention.decisions) {
    return `${attention.decisions} ${attention.decisions === 1 ? 'decision' : 'decisions'} waiting`
  }

  if (attention.safety) {
    return `${attention.safety} protected ${attention.safety === 1 ? 'item needs' : 'items need'} review`
  }

  return `${attention.blockers} ${attention.blockers === 1 ? 'blocker needs' : 'blockers need'} attention`
}

type PersonalAssistantSituationProps = {
  onOpenChange: (open: boolean) => void
  onOpenChat: () => void
  onReviewInChat: (prompt: string) => void
  open: boolean
  showAttentionStrip?: boolean
}

export function PersonalAssistantSituation({
  onOpenChange,
  onOpenChat,
  onReviewInChat,
  open,
  showAttentionStrip = true
}: PersonalAssistantSituationProps) {
  const state = useStore($personalAssistantState)
  const threadScrolledUp = useStore($threadScrolledUp)
  const [proposalIndex, setProposalIndex] = useState(0)

  useEffect(() => {
    if (!open) {
      return
    }
    void refreshPersonalAssistantState().catch(() => undefined)
    const interval = window.setInterval(() => {
      void refreshPersonalAssistantState().catch(() => undefined)
    }, 10_000)

    return () => window.clearInterval(interval)
  }, [open])

  useEffect(() => {
    let acknowledgementInFlight = false

    const acknowledgeIfRead = () => {
      const viewport = document.querySelector('[data-slot="aui_thread-viewport"]')

      if (
        state?.unreadCount &&
        !acknowledgementInFlight &&
        !threadScrolledUp &&
        viewport?.getAttribute('data-following') === 'true' &&
        document.visibilityState === 'visible'
      ) {
        acknowledgementInFlight = true
        void acknowledgePersonalAssistantRead()
          .catch(() => undefined)
          .finally(() => {
            acknowledgementInFlight = false
          })
      }
    }

    acknowledgeIfRead()
    const observer = new MutationObserver(acknowledgeIfRead)

    observer.observe(document.body, {
      attributeFilter: ['data-following'],
      attributes: true,
      childList: true,
      subtree: true
    })
    document.addEventListener('visibilitychange', acknowledgeIfRead)

    return () => {
      observer.disconnect()
      document.removeEventListener('visibilitychange', acknowledgeIfRead)
    }
  }, [state?.unreadCount, state?.version, threadScrolledUp])

  if (!state) {
    return null
  }

  const pendingApprovals = state.pendingApprovals.filter(item => !item.status || item.status === 'pending')
  const captureProposals = state.captureProposals.filter(item => !item.status || item.status === 'pending')
  const boundedProposalIndex = Math.min(proposalIndex, Math.max(captureProposals.length - 1, 0))
  const visibleCaptureProposal = captureProposals[boundedProposalIndex]
  const attention = getPersonalAssistantAttention(state)
  const hasSafety = Boolean(state.latestCoverageReceipt || (state.protectedItems ?? []).length)

  const hasCurrentPicture = Boolean(
    state.outcomes.length || state.commitments.length || state.capacity.summary || state.focus
  )

  const hasRememberedContext = Boolean((state.preferences ?? []).length || state.deferred.length)

  const reviewInChat = (prompt: string) => {
    onReviewInChat(prompt)
    onOpenChange(false)
  }

  return (
    <>
      {showAttentionStrip && attention.total > 0 && (
        <aside
          className="relative z-10 shrink-0 border-b border-(--ui-stroke-tertiary) bg-(--ui-sidebar-surface-background) px-3 py-1.5"
          dir="ltr"
        >
          <button
            aria-label="Review assistant context"
            className="group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-start hover:bg-(--ui-control-hover-background)"
            onClick={() => onOpenChange(true)}
            type="button"
          >
            <span className="grid size-6 shrink-0 place-items-center rounded-full bg-warning/12 text-warning">
              <Codicon name="bell-dot" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-xs font-medium text-(--ui-text-primary)">{attentionHeadline(attention)}</span>
              <span className="block text-[0.6875rem] text-(--ui-text-tertiary)">Review what needs your attention</span>
            </span>
            <Codicon className="text-(--ui-text-tertiary) group-hover:text-(--ui-text-primary)" name="chevron-right" />
          </button>
        </aside>
      )}

      <Sheet onOpenChange={onOpenChange} open={open}>
        <SheetContent className="w-full gap-0 sm:max-w-lg" dir="ltr" side="right">
          <SheetHeader className="border-b border-(--ui-stroke-tertiary) px-4 py-4 pr-12">
            <div className="flex items-center gap-2">
              <span className="grid size-7 place-items-center rounded-md bg-primary/10 text-primary">
                <Codicon name="sparkle" />
              </span>
              <SheetTitle>Assistant context</SheetTitle>
              {state.unreadCount > 0 && (
                <span
                  aria-label={`${state.unreadCount} unread personal assistant ${state.unreadCount === 1 ? 'update' : 'updates'}`}
                  className="rounded-full bg-primary px-1.5 text-[0.6875rem] text-primary-foreground"
                >
                  {state.unreadCount}
                </span>
              )}
            </div>
            <SheetDescription>Review decisions, safety, and what the assistant currently remembers.</SheetDescription>
          </SheetHeader>

          <div className="flex-1 space-y-5 overflow-y-auto px-4 py-4">
            {state.watchdog && (
              <section aria-label="Reliability" className="space-y-3">
                <h2 className="text-[0.6875rem] font-semibold uppercase tracking-[0.08em] text-(--ui-text-tertiary)">
                  Reliability
                </h2>
                <div className="rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-control-background) p-3">
                  <div className="flex items-start gap-2.5">
                    <span
                      className={cn(
                        'mt-0.5 size-2.5 shrink-0 rounded-full',
                        state.watchdog.state === 'active'
                          ? 'bg-success'
                          : state.watchdog.state === 'stale'
                            ? 'bg-warning'
                            : 'bg-danger'
                      )}
                    />
                    <div className="min-w-0 flex-1">
                      <h3 className="text-xs font-semibold">
                        Watchdog {state.watchdog.state}
                      </h3>
                      <p className="mt-0.5 text-[0.6875rem] text-(--ui-text-tertiary)">
                        {state.watchdog.watchedSources} signals monitored
                      </p>
                      {state.watchdog.latestEvent && state.watchdog.latestEvent !== 'watchdog_started' && (
                        <div className="mt-2 border-t border-(--ui-stroke-tertiary) pt-2 text-xs">
                          <span className="font-medium">
                            {state.watchdog.latestEvent
                              .split('_')
                              .map((part, index) => (index === 0 ? `${part.charAt(0).toUpperCase()}${part.slice(1)}` : part))
                              .join(' ')}
                          </span>
                          {state.watchdog.latestTool && (
                            <span className="ml-1 text-(--ui-text-tertiary)">· {state.watchdog.latestTool}</span>
                          )}
                        </div>
                      )}
                      <p className="mt-1 text-[0.6875rem] text-(--ui-text-tertiary)">
                        {{
                          cancelled: 'Repair attempt cancelled',
                          candidate_ready: 'Tested candidate ready — not applied',
                          failed: 'Repair attempt failed',
                          none: 'No repair waiting',
                          queued: 'Repair queued',
                          running: 'Repair worker running',
                          timed_out: 'Repair attempt timed out',
                          verifying: 'Checking candidate'
                        }[state.watchdog.repairStatus]}
                      </p>
                      {state.watchdog.repairTaskId && state.watchdog.repairStatus !== 'none' && (
                        <p className="mt-1 break-all text-[0.6875rem] text-(--ui-text-tertiary)">
                          <span>
                            Task {state.watchdog.repairTaskId}
                            {state.watchdog.repairOutcomeCode && (
                              <> · {state.watchdog.repairOutcomeCode.replaceAll('_', ' ')}</>
                            )}
                          </span>
                          {state.watchdog.repairUpdatedAt && (
                            <>
                              {' · '}
                              <time dateTime={state.watchdog.repairUpdatedAt}>
                                {new Date(state.watchdog.repairUpdatedAt).toLocaleString()}
                              </time>
                            </>
                          )}
                        </p>
                      )}
                    </div>
                  </div>
                </div>
              </section>
            )}

            {attention.total > 0 && (
              <section aria-label="Needs attention" className="space-y-3">
                <h2 className="text-[0.6875rem] font-semibold uppercase tracking-[0.08em] text-(--ui-text-tertiary)">
                  Needs attention
                </h2>

                {(pendingApprovals.length > 0 || captureProposals.length > 0) && (
                  <div className="rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-control-background) p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <h3 className="text-xs font-semibold">Decisions</h3>
                        <p className="mt-0.5 text-[0.6875rem] text-(--ui-text-tertiary)">
                          {pendingApprovals.length} {pendingApprovals.length === 1 ? 'approval' : 'approvals'} ·{' '}
                          {captureProposals.length} proposals
                        </p>
                      </div>
                      {pendingApprovals.length > 0 && (
                        <Button
                          onClick={() => reviewInChat('Show me the pending approvals and ask me to decide each one.')}
                          size="xs"
                          type="button"
                          variant="outline"
                        >
                          Review in chat
                        </Button>
                      )}
                    </div>
                    {pendingApprovals.length > 0 && (
                      <ul className="mt-2 space-y-1 text-xs">
                        {pendingApprovals.map(item => (
                          <li
                            className="rounded border border-(--ui-stroke-tertiary) px-2 py-1.5 text-start"
                            dir="auto"
                            key={item.id}
                          >
                            {itemLabel(item)}
                          </li>
                        ))}
                      </ul>
                    )}
                    {captureProposals.length > 0 && (
                      <div className="mt-2 space-y-2">
                        <div className="flex items-center justify-between text-[0.6875rem] text-(--ui-text-tertiary)">
                          <span>
                            {boundedProposalIndex + 1} of {captureProposals.length}
                          </span>
                          <div className="flex gap-1">
                            <Button
                              aria-label="Previous decision"
                              disabled={boundedProposalIndex === 0}
                              onClick={() => setProposalIndex(index => Math.max(0, index - 1))}
                              size="xs"
                              type="button"
                              variant="ghost"
                            >
                              Previous
                            </Button>
                            <Button
                              aria-label="Next decision"
                              disabled={boundedProposalIndex >= captureProposals.length - 1}
                              onClick={() =>
                                setProposalIndex(index => Math.min(captureProposals.length - 1, index + 1))
                              }
                              size="xs"
                              type="button"
                              variant="ghost"
                            >
                              Next
                            </Button>
                          </div>
                        </div>
                        {visibleCaptureProposal && (
                          <ul className="space-y-1.5">
                            <CaptureProposalItem item={visibleCaptureProposal} key={visibleCaptureProposal.id} />
                          </ul>
                        )}
                      </div>
                    )}
                  </div>
                )}

                {hasSafety && (
                  <div className="rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-control-background) p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <h3 className="text-xs font-semibold">Safety sweep</h3>
                        {state.latestCoverageReceipt ? (
                          <>
                            <p className="mt-1 text-xs">
                              {state.latestCoverageReceipt.reviewedItemIds.length} protected{' '}
                              {state.latestCoverageReceipt.reviewedItemIds.length === 1 ? 'item' : 'items'} checked
                            </p>
                            <p
                              className={cn(
                                'mt-1 text-xs font-medium',
                                attention.safety ? 'text-warning' : 'text-success'
                              )}
                            >
                              {state.latestCoverageReceipt.complete
                                ? `${state.latestCoverageReceipt.riskItemIds.length} needs attention`
                                : 'Safety sweep incomplete'}
                            </p>
                          </>
                        ) : (
                          <p className="mt-1 text-xs text-warning">
                            Unverified · {(state.protectedItems ?? []).length} protected
                          </p>
                        )}
                      </div>
                      {attention.safety > 0 && (
                        <Button
                          aria-label="Review safety issues in chat"
                          onClick={() =>
                            reviewInChat(
                              'Review the unresolved protected items and help me decide the next action for each one.'
                            )
                          }
                          size="xs"
                          type="button"
                          variant="outline"
                        >
                          Review in chat
                        </Button>
                      )}
                    </div>
                  </div>
                )}

                <SituationSection items={state.blockers} section="blockers" title="Blockers" />
              </section>
            )}

            {hasCurrentPicture && (
              <section aria-label="Current picture" className="space-y-3 border-t border-(--ui-stroke-tertiary) pt-4">
                <h2 className="text-[0.6875rem] font-semibold uppercase tracking-[0.08em] text-(--ui-text-tertiary)">
                  Current picture
                </h2>
                {(state.capacity.summary || state.focus) && (
                  <section aria-label="Capacity and focus">
                    <h3 className="mb-1 text-[0.6875rem] font-semibold uppercase tracking-wide text-(--ui-text-tertiary)">
                      Capacity & focus
                    </h3>
                    {state.capacity.summary && (
                      <p className="text-start text-xs" dir="auto">
                        {state.capacity.summary}
                      </p>
                    )}
                    {state.focus && (
                      <p className="mt-1 flex min-w-0 gap-1 text-xs font-medium">
                        <span>Focus:</span>
                        <span className="min-w-0 flex-1 text-start" dir="auto">
                          {itemLabel(state.focus)}
                        </span>
                      </p>
                    )}
                  </section>
                )}
                <SituationSection items={state.outcomes} section="outcomes" title="Outcomes" />
                <SituationSection items={state.commitments} section="commitments" title="Commitments" />
              </section>
            )}

            {hasRememberedContext && (
              <section
                aria-label="Remembered context"
                className="space-y-3 border-t border-(--ui-stroke-tertiary) pt-4"
              >
                <h2 className="text-[0.6875rem] font-semibold uppercase tracking-[0.08em] text-(--ui-text-tertiary)">
                  Remembered context
                </h2>
                <SituationSection items={state.preferences ?? []} section="preferences" title="Preferences" />
                <SituationSection items={state.deferred} section="deferred" title="Deferred important work" />
              </section>
            )}
          </div>

          <div className="flex items-center justify-between gap-3 border-t border-(--ui-stroke-tertiary) px-4 py-2">
            <p className="min-w-0 text-[0.6875rem] text-(--ui-text-tertiary)">
              <span className={state.sync.status === 'fresh' ? 'text-success' : 'text-warning'}>
                {state.sync.status}
              </span>
              <span> · Last verified: {state.sync.lastVerifiedAt || 'never'}</span>
            </p>
            <Button onClick={onOpenChat} size="xs" type="button" variant="outline">
              Open assistant chat
            </Button>
          </div>
        </SheetContent>
      </Sheet>
    </>
  )
}
