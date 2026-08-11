/** Completes Electron shutdown after reporting sidecar cleanup failure generically. */
export async function completeAppShutdown(
  stopSidecar: () => Promise<void>,
  reportFailure: () => void,
  authorizeAndQuit: () => void,
): Promise<void> {
  try {
    await stopSidecar()
  } catch {
    reportFailure()
  } finally {
    authorizeAndQuit()
  }
}
