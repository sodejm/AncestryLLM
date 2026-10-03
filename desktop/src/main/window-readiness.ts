/** Detects the exact structured log record proving a renderer window is ready. */
/**
 * Provides the exact structured log line emitted when the renderer is ready for packaged tests.
 */
export const WINDOW_READY_RECORD = '{"event":"ancestryllm.desktop.window-ready","version":1}'

/**
 * Returns whether complete process-output lines contain the exact renderer readiness record.
 */
export function outputContainsWindowReadyRecord(output: string): boolean {
  return output.split(/\r?\n/u).includes(WINDOW_READY_RECORD)
}
