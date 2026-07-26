import { spawn as spawnChild } from 'node:child_process'

type WatchdogChild = {
  once(event: 'error', listener: () => void): unknown
  unref(): void
}

type WatchdogSpawn = (
  command: string,
  args: string[],
  options: { detached: true; stdio: 'ignore'; windowsHide: true }
) => WatchdogChild

type WatchdogStartOptions = {
  platform?: NodeJS.Platform
  spawn?: WatchdogSpawn
}

export function startHermesWatchdogService(options: WatchdogStartOptions = {}): boolean {
  const platform = options.platform ?? process.platform

  if (platform !== 'linux') {
    return false
  }

  const spawn = options.spawn ?? (spawnChild as WatchdogSpawn)

  try {
    const child = spawn(
      'systemctl',
      ['--user', 'start', 'hermes-live-watchdog.service'],
      { detached: true, stdio: 'ignore', windowsHide: true }
    )
    child.once('error', () => {})
    child.unref()
    return true
  } catch {
    return false
  }
}
