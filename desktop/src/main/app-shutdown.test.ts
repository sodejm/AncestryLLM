import { describe, expect, it, vi } from 'vitest'
import { completeAppShutdown } from './app-shutdown'

describe('Electron app shutdown', () => {
  it('reports sidecar cleanup failure without exposing details or blocking quit', async () => {
    const privateFailure = '/private/process-tree-cleanup-failure'
    const reportFailure = vi.fn()
    const authorizeAndQuit = vi.fn()

    await expect(completeAppShutdown(
      async () => { throw new Error(privateFailure) },
      reportFailure,
      authorizeAndQuit,
    )).resolves.toBeUndefined()

    expect(reportFailure).toHaveBeenCalledOnce()
    expect(reportFailure).toHaveBeenCalledWith()
    expect(authorizeAndQuit).toHaveBeenCalledOnce()
    expect(JSON.stringify(reportFailure.mock.calls)).not.toContain(privateFailure)
  })

  it('quits without reporting when sidecar cleanup succeeds', async () => {
    const reportFailure = vi.fn()
    const authorizeAndQuit = vi.fn()

    await completeAppShutdown(async () => undefined, reportFailure, authorizeAndQuit)

    expect(reportFailure).not.toHaveBeenCalled()
    expect(authorizeAndQuit).toHaveBeenCalledOnce()
  })
})
