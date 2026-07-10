import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'

import { DIRTY_UPDATE_ERROR, dirtyUpdateResult, isDirtyStatus } from './update-dirty'

test('isDirtyStatus treats any porcelain output as dirty', () => {
  assert.equal(isDirtyStatus(''), false)
  assert.equal(isDirtyStatus('\n'), false)
  assert.equal(isDirtyStatus(' M apps/desktop/src/app.tsx\n'), true)
  assert.equal(isDirtyStatus('?? apps/desktop/src/new-file.ts\n'), true)
})

test('dirtyUpdateResult is a terminal apply result for the renderer', () => {
  const result = dirtyUpdateResult('/home/u/.hermes/hermes-agent')

  assert.equal(result.ok, false)
  assert.equal(result.dirty, true)
  assert.equal(result.error, DIRTY_UPDATE_ERROR)
  assert.match(result.message, /local changes/)
  assert.match(result.message, /working features/)
  assert.equal(result.hermesRoot, '/home/u/.hermes/hermes-agent')
})

test('desktop apply paths check dirty state before mutating the checkout', () => {
  const source = fs.readFileSync(path.join(import.meta.dirname, 'main.ts'), 'utf8').replace(/\r\n/g, '\n')

  assert.match(source, /async function dirtyUpdateGuard\(updateRoot/)
  assert.match(source, /runGit\(\['status', '--porcelain'\], \{ cwd: updateRoot \}\)/)

  const applyStart = source.indexOf('async function applyUpdates(')
  const updaterLookup = source.indexOf('const updater = resolveUpdaterBinary()', applyStart)
  const applyGuard = source.indexOf('const dirtyResult = await dirtyUpdateGuard(updateRoot)', applyStart)
  assert.ok(applyStart >= 0, 'applyUpdates exists')
  assert.ok(applyGuard > applyStart, 'applyUpdates calls dirtyUpdateGuard')
  assert.ok(updaterLookup > applyGuard, 'applyUpdates checks dirty state before updater handoff')

  const posixStart = source.indexOf('async function applyUpdatesPosixInApp(')
  const hermesLookup = source.indexOf('const hermes = resolveHermesCliBinary(updateRoot)', posixStart)
  const posixGuard = source.indexOf('const dirtyResult = await dirtyUpdateGuard(updateRoot)', posixStart)
  assert.ok(posixStart >= 0, 'applyUpdatesPosixInApp exists')
  assert.ok(posixGuard > posixStart, 'applyUpdatesPosixInApp calls dirtyUpdateGuard')
  assert.ok(hermesLookup > posixGuard, 'POSIX in-app update checks dirty state before hermes update')
})
