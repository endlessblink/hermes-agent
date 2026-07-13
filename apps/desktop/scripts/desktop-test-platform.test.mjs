import assert from 'node:assert/strict'
import test from 'node:test'

import { supportsDesktopBundleValidation } from './desktop-test-platform.mjs'

test('desktop bundle validation supports every packaged Electron target', () => {
  assert.equal(supportsDesktopBundleValidation('darwin'), true)
  assert.equal(supportsDesktopBundleValidation('win32'), true)
  assert.equal(supportsDesktopBundleValidation('linux'), true)
  assert.equal(supportsDesktopBundleValidation('freebsd'), false)
})
