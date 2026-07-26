import assert from 'node:assert/strict'

import { test } from 'vitest'

import { createQuitMenuItem } from './app-quit'

test('Quit Hermes menu item invokes the explicit shutdown callback', () => {
  let quitCalls = 0

  const item = createQuitMenuItem(() => {
    quitCalls += 1
  })

  assert.equal(item.label, 'Quit Hermes')
  assert.equal(item.accelerator, 'CommandOrControl+Q')
  item.click()
  assert.equal(quitCalls, 1)
})
