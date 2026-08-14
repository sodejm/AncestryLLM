/** Validates and opens external HTTPS links only after explicit confirmation. */
export type ExternalLinkResult = Readonly<{ status: 'opened' | 'cancelled' }>

const MAX_EXTERNAL_LINK_CHARACTERS = 2_048
// eslint-disable-next-line no-control-regex
const CONTROL_CHARACTER = /[\u0000-\u001f\u007f]/u

export function validateExternalLink(value: string): string {
  if (
    typeof value !== 'string'
    || Array.from(value).length < 1
    || Array.from(value).length > MAX_EXTERNAL_LINK_CHARACTERS
    || CONTROL_CHARACTER.test(value)
    || !value.startsWith('https://')
    || value.trim() !== value
    || value.includes('\\')
  ) throw new Error('External link denied')
  let url: URL
  try {
    url = new URL(value)
  } catch {
    throw new Error('External link denied')
  }
  if (url.protocol !== 'https:' || !url.hostname || url.username || url.password || url.port) {
    throw new Error('External link denied')
  }
  return url.href
}

interface ExternalLinkOperations {
  confirm(destination: string): Promise<boolean>
  openExternal(destination: string): Promise<void>
}

export function externalLinkPrompt(destination: string) {
  return Object.freeze({
    type: 'warning' as const,
    buttons: ['Cancel', 'Open link'],
    defaultId: 0,
    cancelId: 0,
    noLink: true,
    title: 'Open external link',
    message: 'Open this destination outside AncestryLLM?',
    detail: `Destination:\n${destination}\n\nOnly continue if you intended to leave the private desktop app.`,
  })
}

export async function openExternalLinkWithConfirmation(
  value: string,
  operations: ExternalLinkOperations,
): Promise<ExternalLinkResult> {
  const destination = validateExternalLink(value)
  if (!await operations.confirm(destination)) return Object.freeze({ status: 'cancelled' })
  await operations.openExternal(destination)
  return Object.freeze({ status: 'opened' })
}
