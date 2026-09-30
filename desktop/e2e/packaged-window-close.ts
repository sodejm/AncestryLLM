/** Verifies the packaged application's final native window shutdown. */

/** Accepts WebdriverIO's terminal session response only after both processes exit. */
export async function closeFinalWindowAndVerifyExit(
  closeWindow: () => Promise<unknown>,
  processIds: readonly number[],
  verifyExit: (pid: number) => Promise<void>,
): Promise<void> {
  try {
    await closeWindow()
  } catch (error) {
    if (!(error instanceof Error)
      || !error.message.includes('All window handles were removed, causing WebdriverIO to close the session.')) {
      throw error
    }
  }
  await Promise.all(processIds.map((pid) => verifyExit(pid)))
}
