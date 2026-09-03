import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path, { dirname } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const DESKTOP_ROOT = path.resolve(HERE, '..')
const SCRIPT = path.join(HERE, 'dev-existing-state.mjs')

test('prints resolved runtime for an existing Linux-style state layout', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-dev-existing-state-'))
  const hermesHome = path.join(tempRoot, 'hermes-home')
  const userData = path.join(tempRoot, 'user-data')
  fs.mkdirSync(path.join(hermesHome, 'profiles'), { recursive: true })
  fs.mkdirSync(userData, { recursive: true })
  fs.writeFileSync(path.join(userData, 'connections.json'), '{"primary":"local"}')

  const stdout = execFileSync(process.execPath, [SCRIPT, '--print-env'], {
    cwd: DESKTOP_ROOT,
    encoding: 'utf8',
    env: {
      ...process.env,
      HERMES_HOME: hermesHome,
      HERMES_DESKTOP_USER_DATA_DIR: userData,
    },
  })

  const parsed = JSON.parse(stdout)
  assert.equal(parsed.HERMES_HOME, hermesHome)
  assert.equal(parsed.HERMES_DESKTOP_USER_DATA_DIR, userData)
  assert.match(parsed.HERMES_DESKTOP_HERMES_ROOT, /hermes-port-rtl$/)
})
