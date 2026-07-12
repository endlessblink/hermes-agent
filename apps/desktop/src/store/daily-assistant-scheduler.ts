const JERUSALEM_TIME_ZONE = 'Asia/Jerusalem'

interface ProfileStore {
  get(): string
  subscribe(listener: (value: string) => void): () => void
}

interface LocalParts {
  day: number
  hour: number
  minute: number
  month: number
  second: number
  year: number
}

const formatter = new Intl.DateTimeFormat('en-CA', {
  day: '2-digit',
  hour: '2-digit',
  hourCycle: 'h23',
  minute: '2-digit',
  month: '2-digit',
  second: '2-digit',
  timeZone: JERUSALEM_TIME_ZONE,
  year: 'numeric'
})

function localParts(date: Date): LocalParts {
  const values = Object.fromEntries(
    formatter
      .formatToParts(date)
      .filter(part => part.type !== 'literal')
      .map(part => [part.type, Number(part.value)])
  )

  return values as unknown as LocalParts
}

function jerusalemLocalToDate(year: number, month: number, day: number, hour: number): Date {
  const desiredWallClock = Date.UTC(year, month - 1, day, hour, 0, 0)
  let instant = desiredWallClock

  // Resolve the zone offset at the target wall time. A second pass handles
  // offset changes near DST boundaries without hard-coding Israel's rules.
  for (let pass = 0; pass < 3; pass += 1) {
    const seen = localParts(new Date(instant))
    const seenWallClock = Date.UTC(seen.year, seen.month - 1, seen.day, seen.hour, seen.minute, seen.second)
    instant += desiredWallClock - seenWallClock
  }

  return new Date(instant)
}

export function nextJerusalemNine(now = new Date()): Date {
  const local = localParts(now)
  const beforeNine = local.hour < 9
  const calendar = new Date(Date.UTC(local.year, local.month - 1, local.day + (beforeNine ? 0 : 1)))

  return jerusalemLocalToDate(
    calendar.getUTCFullYear(),
    calendar.getUTCMonth() + 1,
    calendar.getUTCDate(),
    9
  )
}

export function startDailyAssistantScheduler(
  profile: ProfileStore,
  launch: (profile: string, trigger: 'scheduled') => Promise<void>
): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null

  const clear = () => {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  const schedule = (selected: string, catchUp = true) => {
    clear()

    if (selected !== 'office-work') {
      return
    }

    const now = new Date()
    const local = localParts(now)

    if (catchUp && local.hour >= 9) {
      void launch(selected, 'scheduled')
    }

    const target = nextJerusalemNine(now)

    timer = setTimeout(() => {
      timer = null
      const active = profile.get()

      if (active === 'office-work') {
        void launch(active, 'scheduled')
      }

      schedule(active, false)
    }, Math.max(0, target.getTime() - now.getTime()))
  }

  const unsubscribe = profile.subscribe(schedule)

  return () => {
    unsubscribe()
    clear()
  }
}
