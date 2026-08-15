/** Verifies packaged shutdown diagnostics stay bounded and strictly allowlisted. */
import { describe, expect, it } from 'vitest'

import { APP_SHUTDOWN_DIAGNOSTICS } from '../src/main/shutdown-diagnostics'
import { ShutdownDiagnosticCapture } from './shutdown-diagnostic-capture'

describe('packaged shutdown diagnostic capture', () => {
  it('captures exact newline-framed allowlisted records across stream chunks', () => {
    const capture = new ShutdownDiagnosticCapture()

    capture.consume('stdout', Buffer.from('ignored startup output\nANCESTRYLLM_SHUT'))
    capture.consume('stdout', Buffer.from('DOWN_PHASE=REQUESTED\r\n'))
    capture.consume('stderr', Buffer.from(`${APP_SHUTDOWN_DIAGNOSTICS.jobsPrepared}\n`))

    expect(capture.snapshot()).toEqual([
      APP_SHUTDOWN_DIAGNOSTICS.requested,
      APP_SHUTDOWN_DIAGNOSTICS.jobsPrepared,
    ])
  })

  it('discards arbitrary, partial, and decorated process output', () => {
    const capture = new ShutdownDiagnosticCapture()
    const privateOutput = 'token=private /Users/private/runtime'

    capture.consume('stderr', Buffer.from([
      privateOutput,
      `prefix ${APP_SHUTDOWN_DIAGNOSTICS.sidecarStopped}`,
      `${APP_SHUTDOWN_DIAGNOSTICS.exitAuthorized} suffix`,
      APP_SHUTDOWN_DIAGNOSTICS.sidecarTerminationFailure.slice(0, -1),
      '',
    ].join('\n')))

    expect(capture.snapshot()).toEqual([])
    expect(JSON.stringify(capture.snapshot())).not.toContain(privateOutput)
    expect(capture.timeoutContext()).toBe('shutdown_diagnostics=[]')
    expect(capture.timeoutContext()).not.toContain(privateOutput)
  })

  it('retains only the latest bounded allowlisted records', () => {
    const capture = new ShutdownDiagnosticCapture()

    for (let index = 0; index < 20; index += 1) {
      capture.consume('stdout', Buffer.from(`${APP_SHUTDOWN_DIAGNOSTICS.requested}\n`))
    }

    expect(capture.snapshot()).toHaveLength(16)
  })

  it('discards an overlong split line through its newline before resuming', () => {
    const capture = new ShutdownDiagnosticCapture()

    capture.consume('stdout', Buffer.from('x'.repeat(300)))
    capture.consume('stdout', Buffer.from(`${APP_SHUTDOWN_DIAGNOSTICS.requested}\n`))

    expect(capture.snapshot()).toEqual([])

    capture.consume('stdout', Buffer.from(`${APP_SHUTDOWN_DIAGNOSTICS.requested}\n`))
    expect(capture.snapshot()).toEqual([APP_SHUTDOWN_DIAGNOSTICS.requested])
  })
})
