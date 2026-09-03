#!/usr/bin/env node

import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import os from 'node:os'
import path, { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const DESKTOP_ROOT = resolve(HERE, '..')
const REPO_ROOT = resolve(DESKTOP_ROOT, '..', '..')

function usage() {
  console.log(`Usage: node scripts/dev-existing-state.mjs [--print-env]

Launch Hermes Desktop from this checkout against the existing local Hermes data:
  - HERMES_DESKTOP_HERMES_ROOT -> this checkout
  - HERMES_HOME                -> current local Hermes home
  - HERMES_DESKTOP_USER_DATA_DIR -> current desktop userData

This launcher uses the built desktop renderer, not the Vite dev server, so
Electron reuses the same packaged-style renderer storage boundary as the
installed app.

Options:
  --print-env   print the resolved environment and exit
  -h, --help    show this help`)
}

function defaultUserDataDir() {
  if (process.platform === 'darwin') {
    return path.join(os.homedir(), 'Library', 'Application Support', 'Hermes')
  }

  if (process.platform === 'win32') {
    const appData = process.env.APPDATA?.trim()
    return path.join(appData && appData.length > 0 ? appData : path.join(os.homedir(), 'AppData', 'Roaming'), 'Hermes')
  }

  const xdgConfigHome = process.env.XDG_CONFIG_HOME?.trim()
  return path.join(xdgConfigHome && xdgConfigHome.length > 0 ? xdgConfigHome : path.join(os.homedir(), '.config'), 'Hermes')
}

function parseArgs(argv) {
  const options = { printEnv: false }

  for (const arg of argv) {
    if (arg === '--print-env') {
      options.printEnv = true
      continue
    }

    if (arg === '-h' || arg === '--help') {
      usage()
      process.exit(0)
    }

    throw new Error(`unknown option: ${arg}`)
  }

  return options
}

function resolvedRuntime() {
  const hermesHome = resolve(process.env.HERMES_HOME?.trim() || path.join(os.homedir(), '.hermes'))
  const userDataDir = resolve(process.env.HERMES_DESKTOP_USER_DATA_DIR?.trim() || defaultUserDataDir())

  return {
    HERMES_DESKTOP_HERMES_ROOT: REPO_ROOT,
    HERMES_HOME: hermesHome,
    HERMES_DESKTOP_USER_DATA_DIR: userDataDir,
    HERMES_DESKTOP_RECOVERY_MODE: 'existing-state',
  }
}

function validate(runtime) {
  if (!existsSync(path.join(runtime.HERMES_HOME, 'profiles'))) {
    throw new Error(`Hermes home does not look initialized: ${runtime.HERMES_HOME}`)
  }

  if (!existsSync(path.join(runtime.HERMES_DESKTOP_USER_DATA_DIR, 'connections.json'))) {
    console.warn(
      `[dev-existing-state] warning: no connections.json under ${runtime.HERMES_DESKTOP_USER_DATA_DIR}; Desktop may open first-run state`
    )
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2))
  const runtime = resolvedRuntime()
  validate(runtime)

  if (options.printEnv) {
    console.log(JSON.stringify(runtime, null, 2))
    return
  }

  console.log('[dev-existing-state] Launching Hermes Desktop with existing local state')
  console.log(`[dev-existing-state] source: ${runtime.HERMES_DESKTOP_HERMES_ROOT}`)
  console.log(`[dev-existing-state] HERMES_HOME: ${runtime.HERMES_HOME}`)
  console.log(`[dev-existing-state] userData: ${runtime.HERMES_DESKTOP_USER_DATA_DIR}`)
  console.log(
    '[dev-existing-state] Using the built renderer path so packaged-app local storage stays visible in this checkout.'
  )
  console.log('[dev-existing-state] Close other Hermes windows first if you hit the single-instance lock.')

  const child = spawn('npm', ['run', 'start'], {
    cwd: DESKTOP_ROOT,
    env: {
      ...process.env,
      ...runtime,
    },
    stdio: 'inherit',
  })

  child.on('exit', (code, signal) => {
    if (signal) {
      process.kill(process.pid, signal)
      return
    }

    process.exit(code ?? 0)
  })
}

main().catch((error) => {
  console.error(`[dev-existing-state] ${error instanceof Error ? error.message : String(error)}`)
  process.exit(1)
})
