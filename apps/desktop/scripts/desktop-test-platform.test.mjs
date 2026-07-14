import assert from 'node:assert/strict'
import test from 'node:test'

import {
  desktopExecutableName,
  desktopTestLaunchArgs,
  supportsDesktopBundleValidation
} from './desktop-test-platform.mjs'

test('desktop bundle validation supports every packaged Electron target', () => {
  assert.equal(supportsDesktopBundleValidation('darwin'), true)
  assert.equal(supportsDesktopBundleValidation('win32'), true)
  assert.equal(supportsDesktopBundleValidation('linux'), true)
  assert.equal(supportsDesktopBundleValidation('freebsd'), false)
})

test('desktop validator uses electron-builder executable names', () => {
  assert.equal(desktopExecutableName('darwin'), 'Hermes')
  assert.equal(desktopExecutableName('linux'), 'Hermes')
  assert.equal(desktopExecutableName('win32'), 'Hermes.exe')
})

test('unpacked Linux test launches bypass the unavailable setuid sandbox helper', () => {
  assert.deepEqual(desktopTestLaunchArgs('linux'), ['--no-sandbox'])
  assert.deepEqual(desktopTestLaunchArgs('darwin'), [])
  assert.deepEqual(desktopTestLaunchArgs('win32'), [])
})
