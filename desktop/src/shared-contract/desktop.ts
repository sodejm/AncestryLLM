export const DESKTOP_PROTOCOL_VERSION = '1' as const
export type DesktopTheme = 'system' | 'light' | 'dark'
export interface StartupData {
  applicationName: 'AncestryLLM'
  buildChannel: 'development'
  theme: DesktopTheme
  welcomeMessage: string
  diagnosticSummary: 'Ready'
}
export interface BridgeError { code: 'DESKTOP_UNAVAILABLE'; message: string; remediation: string }
export type BridgeResult<T> =
  | Readonly<{ ok: true; protocolVersion: typeof DESKTOP_PROTOCOL_VERSION; data: Readonly<T> }>
  | Readonly<{ ok: false; protocolVersion: typeof DESKTOP_PROTOCOL_VERSION; error: Readonly<BridgeError> }>
export interface AncestryBridge {
  startup(): Promise<BridgeResult<StartupData>>
  setTheme(theme: DesktopTheme): Promise<BridgeResult<StartupData>>
}
export const desktopChannels = { startup: 'ancestry:desktop:startup', setTheme: 'ancestry:desktop:set-theme' } as const
