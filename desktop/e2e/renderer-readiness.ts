/** Coordinates renderer text reads with the WebDriver readiness contract. */

type WaitUntil = (
  condition: () => Promise<boolean>,
  options: Readonly<{ timeoutMsg: string }>,
) => Promise<unknown>

/**
 * Wait for renderer text that matches the expected first-run content.
 *
 * @param read Reads the currently rendered text.
 * @param expected Identifies content that proves the renderer is readable.
 * @param waitUntil Uses the caller's configured WebDriver timeout and interval.
 * @param timeoutMsg Describes a readiness timeout without capturing page content.
 * @returns The first matching renderer text.
 */
export async function waitForExpectedText(
  read: () => Promise<string>,
  expected: RegExp,
  waitUntil: WaitUntil,
  timeoutMsg: string,
): Promise<string> {
  let observed = ''
  await waitUntil(async () => {
    observed = await read()
    return new RegExp(expected.source, expected.flags).test(observed)
  }, { timeoutMsg })
  return observed
}
