/** Tests the exact newline-framed window-ready marker used by packaged shell verification. */
import { describe, expect, it } from 'vitest'
import { WINDOW_READY_RECORD, outputContainsWindowReadyRecord } from './window-readiness'

describe('window readiness evidence', () => {
  it('does not mistake helper-only output for a ready renderer window', () => {
    expect(outputContainsWindowReadyRecord([
      'sidecar ready',
      'crashpad helper started',
      'browser process running',
      '',
    ].join('\n'))).toBe(false)
  })

  it('requires the complete newline-framed window-ready record', () => {
    expect(outputContainsWindowReadyRecord(`${WINDOW_READY_RECORD}\n`)).toBe(true)
    expect(outputContainsWindowReadyRecord(WINDOW_READY_RECORD)).toBe(true)
    expect(outputContainsWindowReadyRecord(`prefix ${WINDOW_READY_RECORD}\n`)).toBe(false)
    expect(outputContainsWindowReadyRecord(`${WINDOW_READY_RECORD.slice(0, -1)}\n`)).toBe(false)
  })
})
