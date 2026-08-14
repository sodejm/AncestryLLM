/** Configures deterministic single-worker desktop end-to-end test execution. */
import { defineConfig } from '@playwright/test'

export default defineConfig({ testDir: './e2e', workers: 1, retries: 0, timeout: 30_000 })
