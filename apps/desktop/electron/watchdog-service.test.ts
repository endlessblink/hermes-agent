import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import test from 'node:test'

import { startHermesWatchdogService } from './watchdog-service'

test('Linux Hermes startup requests the persistent watchdog service', () => {
  const child = new EventEmitter() as EventEmitter & { unrefCalled: boolean; unref(): void }
  child.unrefCalled = false
  child.unref = () => {
    child.unrefCalled = true
  }
  const calls: unknown[][] = []
  const started = startHermesWatchdogService({
    platform: 'linux',
    spawn: (...args) => {
      calls.push(args)
      return child
    }
  })
  assert.equal(started, true)
  assert.deepEqual(calls, [['systemctl', ['--user', 'start', 'hermes-live-watchdog.service'], { detached: true, stdio: 'ignore', windowsHide: true }]])
  assert.equal(child.unrefCalled, true)
  assert.equal(child.listenerCount('error'), 1)
})

test('non-Linux Hermes startup leaves the user service alone', () => {
  let called = false
  const started = startHermesWatchdogService({
    platform: 'darwin',
    spawn: () => {
      called = true
      throw new Error('must not be called')
    }
  })
  assert.equal(started, false)
  assert.equal(called, false)
})

test('watchdog startup failure never prevents Hermes from opening', () => {
  const started = startHermesWatchdogService({
    platform: 'linux',
    spawn: () => {
      throw new Error('systemctl unavailable')
    }
  })
  assert.equal(started, false)
})
