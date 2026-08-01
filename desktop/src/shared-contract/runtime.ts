import { DESKTOP_PROTOCOL_VERSION, type BridgeResult, type DesktopTheme, type StartupData } from './desktop'

const themes: readonly DesktopTheme[] = ['system', 'light', 'dark']
const exactKeys = (value: Record<string, unknown>, keys: string[]): boolean =>
  Object.keys(value).length === keys.length && keys.every((key) => key in value)
const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const bounded = (value: unknown, max = 240): value is string => typeof value === 'string' && value.length > 0 && value.length <= max

export function parseTheme(value: unknown): DesktopTheme {
  if (typeof value !== 'string' || !themes.includes(value as DesktopTheme)) throw new Error('Invalid desktop theme')
  return value as DesktopTheme
}

export function parseBridgeResult(value: unknown): BridgeResult<StartupData> {
  if (!record(value) || value.protocolVersion !== DESKTOP_PROTOCOL_VERSION || typeof value.ok !== 'boolean') throw new Error('Invalid bridge response')
  if (value.ok) {
    if (!exactKeys(value, ['ok', 'protocolVersion', 'data']) || !record(value.data) ||
      !exactKeys(value.data, ['applicationName', 'buildChannel', 'theme', 'welcomeMessage', 'diagnosticSummary']) ||
      value.data.applicationName !== 'AncestryLLM' || value.data.buildChannel !== 'development' ||
      value.data.diagnosticSummary !== 'Ready' || !bounded(value.data.welcomeMessage, 160)) throw new Error('Invalid bridge response')
    parseTheme(value.data.theme)
    return value as unknown as BridgeResult<StartupData>
  }
  if (!exactKeys(value, ['ok', 'protocolVersion', 'error']) || !record(value.error) ||
    !exactKeys(value.error, ['code', 'message', 'remediation']) || value.error.code !== 'DESKTOP_UNAVAILABLE' ||
    !bounded(value.error.message) || !bounded(value.error.remediation)) throw new Error('Invalid bridge response')
  return value as unknown as BridgeResult<StartupData>
}
