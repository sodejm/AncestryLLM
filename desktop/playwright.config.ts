/** Configures deterministic documentation screenshot capture only. */
import { defineConfig } from '@playwright/test'

/** Keeps Playwright bounded to the reviewed documentation screenshot adapter. */
export default defineConfig({
  testDir: './e2e',
  testMatch: 'docs-screenshots.spec.ts',
  workers: 1,
  retries: 0,
  timeout: 30_000,
})
