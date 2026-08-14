/** Verifies the packaged-application deadline helper rejects stalled operations. */
import { describe, expect, it } from 'vitest'

import { withinDeadline } from './packaged-deadline'

describe('withinDeadline', () => {
  it('rejects a stalled packaged launch at its explicit deadline', async () => {
    await expect(withinDeadline(
      'launching a packaged application',
      10,
      () => new Promise<never>(() => undefined),
    )).rejects.toThrow('Timed out while launching a packaged application after 10ms.')
  })
})
