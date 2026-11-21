/** Keeps process-observation cost out of packaged startup measurements. */

import { describe, expect, it } from 'vitest'
import { measureWarmLaunchMs } from './packaged-launch-metrics'

describe('packaged warm launch measurement', () => {
  it('records renderer readiness before a slow process lookup', async () => {
    let clock = 1_000
    const calls: string[] = []

    const elapsed = await measureWarmLaunchMs(
      async () => {
        calls.push('ready')
        clock = 1_800
      },
      async () => {
        calls.push('process lookup')
        clock = 48_000
        return 1_200
      },
      () => clock,
    )

    expect(calls).toEqual(['ready', 'process lookup'])
    expect(elapsed).toBe(600)
  })
})
