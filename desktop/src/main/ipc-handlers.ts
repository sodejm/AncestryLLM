/** Registers the desktop IPC bridge and validates renderer requests and main-process responses. */
import { DESKTOP_PROTOCOL_VERSION, desktopChannels, type AncestryBridge, type BridgeResult, type PreferenceUpdate } from '../shared-contract/desktop'
import {
  parseAppInfoResult,
  parseCapabilitiesResult,
  parsePreferenceUpdate,
  parsePreferencesResult,
  parseStartupDiagnosticsResult,
} from '../shared-contract/runtime'

type IpcHandler = (event: unknown, ...args: unknown[]) => Promise<unknown>
export interface IpcRegistrar { handle(channel: string, handler: IpcHandler): void }
export type TrustedSender = (event: unknown) => boolean

function error<T>(
  code: 'INVALID_REQUEST' | 'UNAUTHORIZED_SENDER' | 'INVALID_RESPONSE' | 'INTERNAL_ERROR',
  message: string,
  remediation: string,
): BridgeResult<T> {
  return Object.freeze({
    ok: false,
    protocolVersion: DESKTOP_PROTOCOL_VERSION,
    error: Object.freeze({ code, message, remediation }),
  })
}

const unauthorized = <T>(): BridgeResult<T> =>
  error('UNAUTHORIZED_SENDER', 'The desktop request was denied.', 'Reload the AncestryLLM window.')
const invalidRequest = <T>(): BridgeResult<T> =>
  error('INVALID_REQUEST', 'The desktop request was invalid.', 'Reload the AncestryLLM window and try again.')
const invalidResponse = <T>(): BridgeResult<T> =>
  error('INVALID_RESPONSE', 'The desktop response was invalid.', 'Restart AncestryLLM.')
const internalError = <T>(): BridgeResult<T> =>
  error('INTERNAL_ERROR', 'The desktop request could not be completed.', 'Try again or restart AncestryLLM.')

function registerNoArgumentHandler<T>(
  ipc: IpcRegistrar,
  channel: string,
  trusted: TrustedSender,
  operation: () => Promise<unknown>,
  parseResponse: (value: unknown) => BridgeResult<T>,
): void {
  ipc.handle(channel, async (event, ...args) => {
    if (!trusted(event)) return unauthorized<T>()
    if (args.length !== 0) return invalidRequest<T>()
    let response: unknown
    try { response = await operation() } catch { return internalError<T>() }
    try { return parseResponse(response) } catch { return invalidResponse<T>() }
  })
}

export function registerDesktopIpcHandlers(
  ipc: IpcRegistrar,
  bridge: AncestryBridge,
  trusted: TrustedSender,
): void {
  registerNoArgumentHandler(ipc, desktopChannels.getAppInfo, trusted, () => bridge.getAppInfo(), parseAppInfoResult)
  registerNoArgumentHandler(ipc, desktopChannels.getStartupDiagnostics, trusted, () => bridge.getStartupDiagnostics(), parseStartupDiagnosticsResult)
  registerNoArgumentHandler(ipc, desktopChannels.getCapabilities, trusted, () => bridge.getCapabilities(), parseCapabilitiesResult)
  registerNoArgumentHandler(ipc, desktopChannels.retrySidecar, trusted, () => bridge.retrySidecar(), parseStartupDiagnosticsResult)
  registerNoArgumentHandler(ipc, desktopChannels.getPreferences, trusted, () => bridge.getPreferences(), parsePreferencesResult)
  ipc.handle(desktopChannels.updatePreferences, async (event, ...args) => {
    if (!trusted(event)) return unauthorized()
    if (args.length !== 1) return invalidRequest()
    let update: PreferenceUpdate
    try { update = parsePreferenceUpdate(args[0]) } catch { return invalidRequest() }
    let response: unknown
    try { response = await bridge.updatePreferences(update) } catch { return internalError() }
    try { return parsePreferencesResult(response) } catch { return invalidResponse() }
  })
}
