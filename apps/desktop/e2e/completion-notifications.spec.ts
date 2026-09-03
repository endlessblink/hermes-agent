import { expect, test } from '@playwright/test'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { MOCK_REPLY, restartMockServer } from './mock-server'

const DISABLE_AUTO_TITLE = 'auxiliary:\n  title_generation:\n    enabled: false'

test.setTimeout(180_000)

test.describe('completion notifications', () => {
  let fixture: MockBackendFixture

  test.beforeAll(async () => {
    restartMockServer()
    fixture = await setupMockBackend({
      extraConfig: DISABLE_AUTO_TITLE,
      mockServer: { holdFirstStreamForPrompt: 'E2E_BACKGROUND_NOTIFICATION' }
    })
    await waitForAppReady(fixture, 120_000)
  })

  test.afterAll(async () => {
    await fixture?.cleanup()
  })

  test('shows a background completion bubble that opens the completed chat', async () => {
    const page = fixture.page
    const composer = page.locator('[contenteditable="true"]').first()

    await composer.waitFor({ state: 'visible', timeout: 10_000 })
    await composer.click()
    await composer.type('E2E_BACKGROUND_NOTIFICATION', { delay: 20 })
    await page.keyboard.press('Enter')
    await fixture.mock.waitForHeldStream()

    await page.locator('[data-slot="sidebar"] button[aria-label="New session"]').first().click()
    fixture.mock.releaseHeldStream()

    const completionBubble = page.getByRole('status').filter({ hasText: MOCK_REPLY })
    await expect(completionBubble).toBeVisible({ timeout: 90_000 })

    await completionBubble.getByRole('button', { name: 'Open chat' }).click()
    await expect(page.locator('[data-slot="aui_thread-viewport"]:visible')).toContainText(MOCK_REPLY)
  })
})
