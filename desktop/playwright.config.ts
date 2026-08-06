/** Configures Playwright end-to-end execution for the desktop Electron shell tests. */
import { defineConfig } from '@playwright/test'

export default defineConfig({ testDir: './e2e', workers: 1, retries: 0, timeout: 30_000 })
