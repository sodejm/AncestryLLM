/** Keeps process-observation overhead out of packaged launch timing. */

/** Records renderer readiness before looking up the process start timestamp. */
export async function measureWarmLaunchMs(
  waitForReady: () => Promise<void>,
  readProcessStartedAt: () => Promise<number>,
  now: () => number = Date.now,
): Promise<number> {
  await waitForReady()
  const readyAt = now()
  const startedAt = await readProcessStartedAt()
  return readyAt - startedAt
}
