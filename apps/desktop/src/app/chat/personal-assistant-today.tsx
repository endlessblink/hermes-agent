import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import {
  fetchPersonalAssistantDayPlan,
  type PersonalAssistantDayBlock,
  type PersonalAssistantDayPlan
} from '@/store/personal-assistant'

const HOUR_HEIGHT = 64
const MINUTE = 60_000

const pad = (value: number) => String(value).padStart(2, '0')

function localDateKey(date: Date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function minutesFromTime(value: string) {
  const match = /^(\d{2}):(\d{2})$/.exec(value)

  if (!match) {
    return null
  }

  const hours = Number(match[1])
  const minutes = Number(match[2])

  return hours <= 23 && minutes <= 59 ? hours * 60 + minutes : null
}

function timeFromMinutes(value: number) {
  const bounded = Math.max(0, Math.min(1439, value))

  return `${pad(Math.floor(bounded / 60))}:${pad(bounded % 60)}`
}

type LaidOutBlock = PersonalAssistantDayBlock & {
  end: number
  lane: number
  laneCount: number
  start: number
}

function layoutBlocks(blocks: PersonalAssistantDayBlock[]): LaidOutBlock[] {
  const normalized = blocks
    .map(block => {
      const start = minutesFromTime(block.startTime)

      if (start === null) {
        return null
      }

      const duration = block.durationMinutes && block.durationMinutes > 0 ? block.durationMinutes : 30

      return { ...block, end: Math.min(1440, start + duration), lane: 0, laneCount: 1, start }
    })
    .filter((block): block is LaidOutBlock => block !== null)
    .sort((left, right) => left.start - right.start || left.end - right.end)

  const output: LaidOutBlock[] = []
  let group: LaidOutBlock[] = []
  let groupEnd = -1

  const finishGroup = () => {
    if (!group.length) {
      return
    }

    const laneEnds: number[] = []

    for (const block of group) {
      const availableLane = laneEnds.findIndex(end => end <= block.start)
      const lane = availableLane === -1 ? laneEnds.length : availableLane

      laneEnds[lane] = block.end
      block.lane = lane
    }

    for (const block of group) {
      block.laneCount = laneEnds.length
      output.push(block)
    }

    group = []
    groupEnd = -1
  }

  for (const block of normalized) {
    if (group.length && block.start >= groupEnd) {
      finishGroup()
    }

    group.push(block)
    groupEnd = Math.max(groupEnd, block.end)
  }

  finishGroup()

  return output
}

function adaptiveRange(blocks: LaidOutBlock[], nowMinutes: number) {
  const earliest = Math.min(nowMinutes, ...blocks.map(block => block.start))
  const latest = Math.max(nowMinutes, ...blocks.map(block => block.end))
  const start = Math.max(0, Math.floor((earliest - 60) / 60) * 60)
  const naturalEnd = Math.min(1440, Math.ceil((latest + 60) / 60) * 60)
  const end = Math.min(1440, Math.max(naturalEnd, start + 6 * 60))

  return { end, start }
}

function Timeline({ blocks, now }: { blocks: PersonalAssistantDayBlock[]; now: Date }) {
  const markerRef = useRef<HTMLDivElement>(null)
  const laidOut = useMemo(() => layoutBlocks(blocks), [blocks])
  const nowMinutes = now.getHours() * 60 + now.getMinutes()
  const range = useMemo(() => adaptiveRange(laidOut, nowMinutes), [laidOut, nowMinutes])
  const hours = Array.from({ length: (range.end - range.start) / 60 + 1 }, (_, index) => range.start + index * 60)
  const height = ((range.end - range.start) / 60) * HOUR_HEIGHT
  const markerTop = ((nowMinutes - range.start) / 60) * HOUR_HEIGHT

  useEffect(() => {
    markerRef.current?.scrollIntoView?.({ block: 'center' })
  }, [])

  return (
    <section aria-label="Today timeline" className="relative min-h-0 flex-1 overflow-y-auto px-4 py-4">
      <div className="relative" style={{ height }}>
        {hours.map(hour => (
          <div
            className="absolute right-0 left-0 border-t border-(--ui-stroke-tertiary)"
            key={hour}
            style={{ top: ((hour - range.start) / 60) * HOUR_HEIGHT }}
          >
            <time className="absolute -top-2.5 left-0 w-12 bg-(--ui-sidebar-surface-background) pr-2 text-right font-mono text-[0.6875rem] text-(--ui-text-tertiary)">
              {timeFromMinutes(hour)}
            </time>
          </div>
        ))}

        {laidOut.map(block => {
          const laneWidth = 100 / block.laneCount
          const top = ((block.start - range.start) / 60) * HOUR_HEIGHT
          const proportionalHeight = ((block.end - block.start) / 60) * HOUR_HEIGHT
          const endTime = timeFromMinutes(block.end)

          return (
            <article
              aria-label={`${block.title}, ${block.startTime} to ${endTime}`}
              className="absolute overflow-hidden rounded-md border border-primary/25 bg-primary/10 px-2.5 py-1.5 text-start shadow-[inset_2px_0_0_var(--primary)]"
              dir="auto"
              key={block.id}
              style={{
                height: Math.max(34, proportionalHeight - 3),
                left: `calc(3.5rem + ${block.lane * laneWidth}%)`,
                top,
                width: `calc(${laneWidth}% - 3.75rem)`
              }}
            >
              <p className="truncate text-xs font-semibold text-(--ui-text-primary)">{block.title}</p>
              <p className="mt-0.5 font-mono text-[0.6875rem] text-(--ui-text-secondary)" dir="ltr">
                {block.startTime}–{endTime}
              </p>
            </article>
          )
        })}

        {markerTop >= 0 && markerTop <= height && (
          <div
            aria-label={`Current time ${timeFromMinutes(nowMinutes)}`}
            className="pointer-events-none absolute right-0 left-12 z-10 border-t border-warning"
            ref={markerRef}
            style={{ top: markerTop }}
          >
            <span className="absolute -top-1 -left-1 size-2 rounded-full bg-warning" />
          </div>
        )}
      </div>
    </section>
  )
}

type PersonalAssistantTodayProps = {
  onOpenChange: (open: boolean) => void
  open: boolean
}

export function PersonalAssistantToday({ onOpenChange, open }: PersonalAssistantTodayProps) {
  const [now, setNow] = useState(() => new Date())
  const [plan, setPlan] = useState<PersonalAssistantDayPlan | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const date = localDateKey(now)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)

    try {
      setPlan(await fetchPersonalAssistantDayPlan(date))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'FlowState day plan is unavailable')
    } finally {
      setLoading(false)
    }
  }, [date])

  useEffect(() => {
    if (!open) {
      return
    }

    void load()
    const timer = window.setInterval(() => setNow(new Date()), MINUTE)

    return () => window.clearInterval(timer)
  }, [load, open])

  return (
    <Sheet onOpenChange={onOpenChange} open={open}>
      <SheetContent className="w-full gap-0 sm:max-w-lg" dir="ltr" side="right">
        <SheetHeader className="border-b border-(--ui-stroke-tertiary) px-5 py-4 pr-12">
          <div className="flex items-center justify-between gap-3">
            <div>
              <SheetTitle>Today</SheetTitle>
              <SheetDescription>
                {now.toLocaleDateString(undefined, { day: 'numeric', month: 'long', weekday: 'long' })} · FlowState
              </SheetDescription>
            </div>
            <Button
              aria-label="Refresh Today plan"
              disabled={loading}
              onClick={() => void load()}
              size="icon-sm"
              type="button"
              variant="ghost"
            >
              <Codicon className={loading ? 'animate-spin' : undefined} name="refresh" />
            </Button>
          </div>
        </SheetHeader>

        {loading && !plan && (
          <div className="space-y-3 px-5 py-5" role="status">
            <div className="h-16 animate-pulse rounded-md bg-(--ui-control-background)" />
            <div className="h-24 animate-pulse rounded-md bg-(--ui-control-background)" />
            <span className="sr-only">Loading today’s FlowState plan</span>
          </div>
        )}

        {error && !plan && (
          <div className="m-5 rounded-md border border-destructive/35 bg-destructive/8 p-4" role="alert">
            <p className="text-sm font-medium">Today’s plan could not be loaded.</p>
            <p className="mt-1 text-xs text-(--ui-text-secondary)">{error}</p>
            <Button aria-label="Retry Today plan" className="mt-3" onClick={() => void load()} size="sm" type="button">
              Retry
            </Button>
          </div>
        )}

        {plan && plan.blocks.length > 0 && <Timeline blocks={plan.blocks} now={now} />}

        {plan && plan.blocks.length === 0 && (
          <div className="grid min-h-72 flex-1 place-items-center px-8 text-center">
            <div>
              <span className="mx-auto grid size-10 place-items-center rounded-full bg-(--ui-control-background)">
                <Codicon className="text-(--ui-text-tertiary)" name="calendar" size="1.125rem" />
              </span>
              <h2 className="mt-4 text-sm font-semibold">No timed work blocks today</h2>
              <p className="mt-1 max-w-64 text-xs leading-relaxed text-(--ui-text-tertiary)">
                FlowState has not scheduled anything by the clock. Untimed tasks stay in FlowState.
              </p>
            </div>
          </div>
        )}

        {plan && (
          <div className="border-t border-(--ui-stroke-tertiary) px-5 py-2 text-[0.6875rem] text-(--ui-text-tertiary)">
            Read-only · Refreshed {plan.capturedAt ? new Date(plan.capturedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'just now'}
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}
