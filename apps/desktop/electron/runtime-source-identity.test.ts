import assert from 'node:assert/strict'
import test from 'node:test'

import { resolveRuntimeSourceIdentity, runtimeSourceRootCandidates } from './runtime-source-identity'

test('local packaged app can recover its enclosing source checkout without launcher environment', () => {
  const candidates = runtimeSourceRootCandidates(
    '/work/hermes/apps/desktop/release/linux-unpacked/Hermes'
  )

  assert.ok(candidates.includes('/work/hermes'))
  assert.equal(candidates[0], '/work/hermes/apps/desktop/release/linux-unpacked')
})

test('runtime source identity accepts only matching fixed-schema output', () => {
  const identity = resolveRuntimeSourceIdentity({
    command: '/repo/.venv/bin/python',
    root: '/repo',
    env: { PYTHONPATH: '/repo' },
    execFileSyncImpl: () =>
      JSON.stringify({
        ok: true,
        schemaVersion: 1,
        root: '/repo',
        revision: 'a'.repeat(40),
        sourceManifestDigest: 'b'.repeat(64)
      })
  })

  assert.deepEqual(identity, {
    HERMES_RUNTIME_BUILD_ID: `source-${'a'.repeat(12)}-${'b'.repeat(12)}`,
    HERMES_RUNTIME_SOURCE_MANIFEST_DIGEST: 'b'.repeat(64),
    HERMES_RUNTIME_SOURCE_ROOT: '/repo'
  })
})

test('runtime source identity fails closed on invalid output', () => {
  assert.equal(
    resolveRuntimeSourceIdentity({
      command: 'python',
      root: '/repo',
      env: {},
      execFileSyncImpl: () => JSON.stringify({ ok: true, root: '/elsewhere' })
    }),
    null
  )
})
