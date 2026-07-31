import { _electron as electron, expect, test } from '@playwright/test'

test('packaged shell exposes only the bounded bridge and keyboard navigation', async () => {
  const app = await electron.launch({ args: ['.'] })
  try {
    const page = await app.firstWindow()
    await expect(page.getByRole('heading', { name: 'Home' })).toBeVisible()
    expect(await page.evaluate(() => typeof (globalThis as { process?: unknown }).process)).toBe('undefined')
    await page.getByRole('link', { name: 'Diagnostics' }).press('Enter')
    await expect(page.getByRole('heading', { name: 'Diagnostics' })).toBeVisible()
    await expect(page.getByRole('main').getByText('Ready', { exact: true })).toBeVisible()
    await expect(page.getByRole('alert')).toHaveCount(0)
    await page.getByRole('link', { name: 'Settings' }).click()
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeFocused()
  } finally { await app.close() }
})
