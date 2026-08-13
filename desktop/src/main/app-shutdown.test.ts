// Verifies Electron quit remains bounded and fails closed while jobs are unsafe.
import { describe, expect, it, vi } from 'vitest'
import { completeAppShutdown, requestVerifiedShutdownBeforeWindowClose } from './app-shutdown'

describe('Electron window shutdown guard', () => {
  it('keeps the final window open until verified shutdown is authorized', () => {
    const event = { preventDefault: vi.fn() }
    const requestQuit = vi.fn()

    requestVerifiedShutdownBeforeWindowClose(event, true, false, requestQuit)

    expect(event.preventDefault).toHaveBeenCalledOnce()
    expect(requestQuit).toHaveBeenCalledOnce()
  })

  it('does not re-enter app quit while verified shutdown is already pending', () => {
    const event = { preventDefault: vi.fn() }
    const requestQuit = vi.fn()

    requestVerifiedShutdownBeforeWindowClose(event, true, true, requestQuit)

    expect(event.preventDefault).toHaveBeenCalledOnce()
    expect(requestQuit).not.toHaveBeenCalled()
  })

  it('allows the final window to close after verified shutdown is authorized', () => {
    const event = { preventDefault: vi.fn() }
    const requestQuit = vi.fn()

    requestVerifiedShutdownBeforeWindowClose(event, false, false, requestQuit)

    expect(event.preventDefault).not.toHaveBeenCalled()
    expect(requestQuit).not.toHaveBeenCalled()
  })
})

describe('Electron app shutdown', () => {
  it('reports job preparation failure without exposing details and stays open', async () => {
    const privateFailure = '/private/process-tree-cleanup-failure'
    const stopSidecar = vi.fn()
    const reportFailure = vi.fn()
    const authorizeAndExit = vi.fn()

    await expect(completeAppShutdown(
      async () => { throw new Error(privateFailure) },
      async () => 'stay',
      stopSidecar,
      reportFailure,
      authorizeAndExit,
    )).resolves.toBe(false)

    expect(stopSidecar).not.toHaveBeenCalled()
    expect(reportFailure).toHaveBeenCalledOnce()
    expect(reportFailure).toHaveBeenCalledWith()
    expect(authorizeAndExit).not.toHaveBeenCalled()
    expect(JSON.stringify(reportFailure.mock.calls)).not.toContain(privateFailure)
  })

  it('prepares jobs before stopping the sidecar and authorizing exit', async () => {
    const order: string[] = []
    const actions: string[] = []
    const reportFailure = vi.fn()
    const authorizeAndExit = vi.fn(() => order.push('exit'))

    await expect(completeAppShutdown(
      async (action) => { actions.push(action); order.push('prepare') },
      vi.fn(),
      async () => { order.push('stop') },
      reportFailure,
      authorizeAndExit,
    )).resolves.toBe(true)

    expect(reportFailure).not.toHaveBeenCalled()
    expect(authorizeAndExit).toHaveBeenCalledOnce()
    expect(actions).toEqual(['wait'])
    expect(order).toEqual(['prepare', 'stop', 'exit'])
  })

  it('accepts unavailable startup without a session as the explicit safe-empty case', async () => {
    const prepareJobs = vi.fn()
    const stopSidecar = vi.fn().mockResolvedValue(undefined)
    const authorizeAndExit = vi.fn()

    await expect(completeAppShutdown(
      prepareJobs,
      vi.fn(),
      stopSidecar,
      vi.fn(),
      authorizeAndExit,
      () => true,
    )).resolves.toBe(true)

    expect(prepareJobs).not.toHaveBeenCalled()
    expect(stopSidecar).toHaveBeenCalledOnce()
    expect(authorizeAndExit).toHaveBeenCalledOnce()
  })

  it('still verifies the sidecar stop for the explicit safe-empty case', async () => {
    const prepareJobs = vi.fn()
    const reportFailure = vi.fn()
    const authorizeAndExit = vi.fn()

    await expect(completeAppShutdown(
      prepareJobs,
      vi.fn(),
      vi.fn().mockRejectedValue(new Error('/private/degraded-process-tree')),
      reportFailure,
      authorizeAndExit,
      () => true,
    )).resolves.toBe(false)

    expect(prepareJobs).not.toHaveBeenCalled()
    expect(reportFailure).toHaveBeenCalledOnce()
    expect(reportFailure).toHaveBeenCalledWith()
    expect(authorizeAndExit).not.toHaveBeenCalled()
  })

  it('lets the user wait again before authorizing exit', async () => {
    const actions: string[] = []
    const prepareJobs = vi.fn(async (action: string) => {
      actions.push(action)
      if (actions.length === 1) throw new Error('still active')
    })
    const chooseUnsafeAction = vi.fn().mockResolvedValue('wait')
    const stopSidecar = vi.fn().mockResolvedValue(undefined)
    const authorizeAndExit = vi.fn()

    await expect(completeAppShutdown(
      prepareJobs,
      chooseUnsafeAction,
      stopSidecar,
      vi.fn(),
      authorizeAndExit,
    )).resolves.toBe(true)

    expect(actions).toEqual(['wait', 'wait'])
    expect(chooseUnsafeAction).toHaveBeenCalledOnce()
    expect(stopSidecar).toHaveBeenCalledOnce()
    expect(authorizeAndExit).toHaveBeenCalledOnce()
  })

  it('requests cancellation only after the user chooses it', async () => {
    const actions: string[] = []
    const prepareJobs = vi.fn(async (action: string) => {
      actions.push(action)
      if (action === 'wait') throw new Error('still active')
    })
    const stopSidecar = vi.fn().mockResolvedValue(undefined)

    await expect(completeAppShutdown(
      prepareJobs,
      vi.fn().mockResolvedValue('cancel'),
      stopSidecar,
      vi.fn(),
      vi.fn(),
    )).resolves.toBe(true)

    expect(actions).toEqual(['wait', 'cancel'])
    expect(stopSidecar).toHaveBeenCalledOnce()
  })

  it('vetoes quit when sidecar process-tree shutdown cannot be verified', async () => {
    const reportFailure = vi.fn()
    const authorizeAndExit = vi.fn()

    await expect(completeAppShutdown(
      vi.fn().mockResolvedValue(undefined),
      vi.fn(),
      vi.fn().mockRejectedValue(new Error('/private/sidecar/process')),
      reportFailure,
      authorizeAndExit,
    )).resolves.toBe(false)

    expect(reportFailure).toHaveBeenCalledOnce()
    expect(reportFailure).toHaveBeenCalledWith()
    expect(authorizeAndExit).not.toHaveBeenCalled()
  })

  it('retries a failed sidecar stop without repeating a successful job preflight', async () => {
    const progress = { jobsPrepared: false }
    const prepareJobs = vi.fn().mockResolvedValue(undefined)
    const stopSidecar = vi.fn()
      .mockRejectedValueOnce(new Error('temporary process-tree verification failure'))
      .mockResolvedValueOnce(undefined)
    const reportFailure = vi.fn()
    const authorizeAndExit = vi.fn()

    await expect(completeAppShutdown(
      prepareJobs,
      vi.fn(),
      stopSidecar,
      reportFailure,
      authorizeAndExit,
      () => false,
      progress,
    )).resolves.toBe(false)
    await expect(completeAppShutdown(
      prepareJobs,
      vi.fn(),
      stopSidecar,
      reportFailure,
      authorizeAndExit,
      () => false,
      progress,
    )).resolves.toBe(true)

    expect(progress.jobsPrepared).toBe(true)
    expect(prepareJobs).toHaveBeenCalledOnce()
    expect(stopSidecar).toHaveBeenCalledTimes(2)
    expect(reportFailure).toHaveBeenCalledOnce()
    expect(authorizeAndExit).toHaveBeenCalledOnce()
  })

  it('fails closed if the shutdown-choice prompt cannot return a valid choice', async () => {
    const stopSidecar = vi.fn()
    const reportFailure = vi.fn()

    await expect(completeAppShutdown(
      vi.fn().mockRejectedValue(new Error('still active')),
      vi.fn().mockRejectedValue(new Error('dialog unavailable')),
      stopSidecar,
      reportFailure,
      vi.fn(),
    )).resolves.toBe(false)

    expect(reportFailure).toHaveBeenCalledOnce()
    expect(stopSidecar).not.toHaveBeenCalled()
  })
})
