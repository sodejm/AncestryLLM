/** Verifies renderer text readiness across native window startup races. */
import { describe, expect, it, vi } from 'vitest'
import { waitForExpectedText } from './renderer-readiness'

describe('waitForExpectedText', () => {
  it('waits through an empty renderer read without changing the WebDriver timeout contract', async () => {
    const read = vi.fn()
      .mockResolvedValueOnce('')
      .mockResolvedValue('Your desktop control shell stays local to this device.')
    const waitUntil = vi.fn(async (
      condition: () => Promise<boolean>,
      options: Readonly<{ timeoutMsg: string }>,
    ) => {
      expect(options).toEqual({ timeoutMsg: 'Expected Welcome content to become readable' })
      expect(await condition()).toBe(false)
      expect(await condition()).toBe(true)
    })

    const result = await waitForExpectedText(
      read,
      /Your desktop control shell stays local to this device\./u,
      waitUntil,
      'Expected Welcome content to become readable',
    )

    expect(result).toBe('Your desktop control shell stays local to this device.')
    expect(read).toHaveBeenCalledTimes(2)
    expect(waitUntil).toHaveBeenCalledTimes(1)
  })
})
