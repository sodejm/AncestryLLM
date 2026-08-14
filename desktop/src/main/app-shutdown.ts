/** Coordinates safe Electron shutdown with active sidecar jobs. */
import type { JobShutdownAction } from './sidecar-client'

/**
 * Represents the user's explicit response when active jobs make shutdown unsafe.
 */
export type UnsafeShutdownChoice = JobShutdownAction | 'stay'
/**
 * Records whether active jobs have already reached a safe terminal state across shutdown retries.
 */
export type AppShutdownProgress = { jobsPrepared: boolean }

type WindowCloseEvent = { preventDefault: () => void }

/** Keeps a native window visible while the verified shutdown handshake runs. */
export function requestVerifiedShutdownBeforeWindowClose(
  event: WindowCloseEvent,
  shutdownRequired: boolean,
  shutdownPending: boolean,
  requestQuit: () => void,
): void {
  if (!shutdownRequired) return
  event.preventDefault()
  if (!shutdownPending) requestQuit()
}

/** Authorizes Electron shutdown only after jobs and the sidecar stop safely. */
export async function completeAppShutdown(
  prepareJobs: (action: JobShutdownAction) => Promise<void>,
  chooseUnsafeAction: () => Promise<UnsafeShutdownChoice>,
  stopSidecar: () => Promise<void>,
  reportFailure: () => void,
  authorizeAndExit: () => void,
  isExplicitSafeEmpty: () => boolean = () => false,
  progress: AppShutdownProgress = { jobsPrepared: false },
): Promise<boolean> {
  if (!progress.jobsPrepared && !isExplicitSafeEmpty()) {
    let action: JobShutdownAction = 'wait'
    while (true) {
      try {
        await prepareJobs(action)
        progress.jobsPrepared = true
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
  }

  try {
    await stopSidecar()
  } catch {
    reportFailure()
    return false
  }
  authorizeAndExit()
  return true
}
