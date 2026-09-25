import { describe, expect, it, vi } from 'vitest'
import { closeFinalWindowAndVerifyExit } from './packaged-window-close'

describe('packaged final window close', () => {
  it('checks both processes after WebdriverIO removes its last window handle', async () => {
    const verifyExit = vi.fn(async (pid: number) => { expect(pid).toBeGreaterThan(0) })
    await closeFinalWindowAndVerifyExit(
      async () => { throw new Error('All window handles were removed, causing WebdriverIO to close the session.') },
      [12, 13],
      verifyExit,
    )
    expect(verifyExit.mock.calls).toEqual([[12], [13]])
  })

  it('propagates other close errors', async () => {
    const verifyExit = vi.fn(async (pid: number) => { expect(pid).toBeGreaterThan(0) })
    await expect(closeFinalWindowAndVerifyExit(
      async () => { throw new Error('Window could not be closed') },
      [12, 13],
      verifyExit,
    )).rejects.toThrow('Window could not be closed')
    expect(verifyExit).not.toHaveBeenCalled()
  })

  it('fails if either process survives a terminal close response', async () => {
    await expect(closeFinalWindowAndVerifyExit(
      async () => { throw new Error('All window handles were removed, causing WebdriverIO to close the session.') },
      [12, 13],
      async (pid) => { if (pid === 13) throw new Error('Process 13 did not exit') },
    )).rejects.toThrow('Process 13 did not exit')
  })
})
