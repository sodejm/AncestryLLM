/** Configures isolated desktop unit tests and React transforms under Vitest. */
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

/** Configures jsdom unit tests, shared setup, coverage thresholds, and reviewed exclusions. */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/renderer/src/test-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}', 'e2e/**/*.test.ts'],
    restoreMocks: true,
  },
})
