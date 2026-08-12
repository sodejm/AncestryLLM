/** Coordinates safe Electron shutdown with active sidecar jobs. */
import type { JobShutdownAction } from './sidecar-client'

export type UnsafeShutdownChoice = JobShutdownAction | 'stay'

/** Authorizes Electron shutdown only after jobs and the sidecar stop safely. */
export async function completeAppShutdown(
  prepareJobs: (action: JobShutdownAction) => Promise<void>,
  chooseUnsafeAction: () => Promise<UnsafeShutdownChoice>,
  stopSidecar: () => Promise<void>,
  reportFailure: () => void,
  authorizeAndQuit: () => void,
): Promise<boolean> {
  let action: JobShutdownAction = 'wait'
  while (true) {
    try {
      await prepareJobs(action)
      break
    } catch {
      reportFailure()
      let choice: UnsafeShutdownChoice
      try {
        choice = await chooseUnsafeAction()
      } catch {
        return false
      }
      if (choice === 'stay') return false
      if (choice !== 'wait' && choice !== 'cancel') return false
      action = choice
    }
  }

  try {
    await stopSidecar()
  } catch {
    reportFailure()
    return false
  }
  authorizeAndQuit()
  return true
}
