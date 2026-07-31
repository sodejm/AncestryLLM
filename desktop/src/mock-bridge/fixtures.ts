import { DESKTOP_PROTOCOL_VERSION, type BridgeResult, type StartupData } from '../shared-contract/desktop'

function deepFreeze<T>(value: T): Readonly<T> {
  if (value && typeof value === 'object') {
    Object.freeze(value)
    for (const item of Object.values(value)) deepFreeze(item)
  }
  return value
}

export const successFixture = deepFreeze<BridgeResult<StartupData>>({
  ok: true,
  protocolVersion: DESKTOP_PROTOCOL_VERSION,
  data: { applicationName: 'AncestryLLM', buildChannel: 'development', theme: 'system', welcomeMessage: 'Your private family history workspace.', diagnosticSummary: 'Ready' },
})
export const failureFixture = deepFreeze<BridgeResult<StartupData>>({
  ok: false,
  protocolVersion: DESKTOP_PROTOCOL_VERSION,
  error: { code: 'DESKTOP_UNAVAILABLE', message: 'Desktop diagnostics are temporarily unavailable.', remediation: 'Restart AncestryLLM.' },
})
