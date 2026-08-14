/** Defines the trusted app protocol, CSP, navigation, and window security policy. */
/**
 * Defines an immutable input to the deny-by-default renderer security policy.
 */
export const APP_ENTRY_URL = 'app://bundle/index.html' as const
/**
 * Lists the only renderer route hashes the application protocol may serve as entry points.
 */
export const APP_ENTRY_ROUTE_HASHES = Object.freeze([
  '#/',
  '#/chat',
  '#/tasks',
  '#/diagnostics',
  '#/settings',
] as const)

/**
 * Defines an immutable input to the deny-by-default renderer security policy.
 */
export const APP_SCHEME_PRIVILEGES = Object.freeze({
  standard: true,
  secure: true,
  supportFetchAPI: false,
  bypassCSP: false,
  allowServiceWorkers: false,
  corsEnabled: false,
  stream: false,
  codeCache: false,
})

/**
 * Defines an immutable input to the deny-by-default renderer security policy.
 */
export const PRODUCTION_CSP = "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; font-src 'self'; connect-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'" as const

/**
 * Defines an immutable input to the deny-by-default renderer security policy.
 */
export const APP_ASSET_MANIFEST = Object.freeze({
  '/index.html': { file: 'index.html', mime: 'text/html; charset=utf-8' },
  '/assets/index.js': { file: 'assets/index.js', mime: 'text/javascript; charset=utf-8' },
  '/assets/index.css': { file: 'assets/index.css', mime: 'text/css; charset=utf-8' },
})

type ManifestPath = keyof typeof APP_ASSET_MANIFEST
type AssetReader = (file: string) => Promise<Uint8Array>
interface ProtocolRequest { method: string; url: string }

/** Applies the production CSP and anti-sniffing headers to every app-protocol response. */
const responseHeaders = (contentType?: string): Record<string, string> => ({
  ...(contentType ? { 'Content-Type': contentType } : {}),
  'Content-Security-Policy': PRODUCTION_CSP,
  'X-Content-Type-Options': 'nosniff',
  'Cache-Control': 'no-store',
})

/**
 * Creates a production app-protocol handler that serves only canonical files below the packaged renderer root.
 */
export function createAppProtocolHandler(readAsset: AssetReader): (request: ProtocolRequest) => Promise<Response> {
  return async (request: ProtocolRequest): Promise<Response> => {
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return new Response(null, { status: 405, headers: responseHeaders() })
    }

    // Production assets have an exact ASCII route table. Reject encoded routes
    // before URL parsing because URL normalizes encoded dot segments.
    if (request.url.includes('%')) {
      return new Response(null, { status: 404, headers: responseHeaders() })
    }

    let url: URL
    try {
      url = new URL(request.url)
    } catch {
      return new Response(null, { status: 404, headers: responseHeaders() })
    }
    if (url.protocol !== 'app:' || url.hostname !== 'bundle' || url.port || url.username || url.password || url.search) {
      return new Response(null, { status: 404, headers: responseHeaders() })
    }
    if (
      url.hash
      && (url.pathname !== '/index.html' || !APP_ENTRY_ROUTE_HASHES.includes(url.hash as typeof APP_ENTRY_ROUTE_HASHES[number]))
    ) {
      return new Response(null, { status: 404, headers: responseHeaders() })
    }

    let decodedPath: string
    try {
      decodedPath = decodeURIComponent(url.pathname)
    } catch {
      return new Response(null, { status: 404, headers: responseHeaders() })
    }
    if (decodedPath !== url.pathname || decodedPath.includes('..') || !(decodedPath in APP_ASSET_MANIFEST)) {
      return new Response(null, { status: 404, headers: responseHeaders() })
    }

    const asset = APP_ASSET_MANIFEST[decodedPath as ManifestPath]
    try {
      const contents = request.method === 'HEAD' ? null : await readAsset(asset.file)
      const body = contents === null ? null : Uint8Array.from(contents).buffer
      return new Response(body, { status: 200, headers: responseHeaders(asset.mime) })
    } catch {
      return new Response(null, { status: 404, headers: responseHeaders() })
    }
  }
}

/**
 * Makes every renderer-isolation BrowserWindow preference explicit and non-optional.
 */
export interface SecureWebPreferences {
  preload: string
  contextIsolation: true
  nodeIntegration: false
  nodeIntegrationInWorker: false
  nodeIntegrationInSubFrames: false
  sandbox: true
  webSecurity: true
  webviewTag: false
  devTools: boolean
  allowRunningInsecureContent: false
  navigateOnDragDrop: false
}

/**
 * Captures the renderer controls recorded in startup and release evidence without exposing secrets.
 */
export interface RuntimeSecurityState {
  mode: 'development' | 'production'
  rendererUrl: string
  contentSecurityPolicy: string | null
  contextIsolation: boolean
  nodeIntegration: boolean
  nodeIntegrationInWorker: boolean
  nodeIntegrationInSubFrames: boolean
  sandbox: boolean
  webSecurity: boolean
  webviewTag: boolean
  devTools: boolean
  permissionsDenied: true
  navigationDenied: true
  childWindowsDenied: true
  downloadsDenied: true
  serviceWorkersAllowed: false
  bypassCsp: false
}

/**
 * Returns the immutable BrowserWindow preferences that preserve renderer isolation and disable Node access.
 */
export function secureWebPreferences(preload: string, production: boolean): SecureWebPreferences {
  return {
    preload,
    contextIsolation: true,
    nodeIntegration: false,
    nodeIntegrationInWorker: false,
    nodeIntegrationInSubFrames: false,
    sandbox: true,
    webSecurity: true,
    webviewTag: false,
    devTools: !production,
    allowRunningInsecureContent: false,
    navigateOnDragDrop: false,
  }
}

/**
 * Records the renderer URL and security controls that must be evidenced for the selected runtime mode.
 */
export function createRuntimeSecurityState(
  rendererUrl: string,
  preferences: SecureWebPreferences,
  production: boolean,
): Readonly<RuntimeSecurityState> {
  return Object.freeze({
    mode: production ? 'production' : 'development',
    rendererUrl,
    contentSecurityPolicy: production ? PRODUCTION_CSP : null,
    contextIsolation: preferences.contextIsolation,
    nodeIntegration: preferences.nodeIntegration,
    nodeIntegrationInWorker: preferences.nodeIntegrationInWorker,
    nodeIntegrationInSubFrames: preferences.nodeIntegrationInSubFrames,
    sandbox: preferences.sandbox,
    webSecurity: preferences.webSecurity,
    webviewTag: preferences.webviewTag,
    devTools: preferences.devTools,
    permissionsDenied: true,
    navigationDenied: true,
    childWindowsDenied: true,
    downloadsDenied: true,
    serviceWorkersAllowed: APP_SCHEME_PRIVILEGES.allowServiceWorkers,
    bypassCsp: APP_SCHEME_PRIVILEGES.bypassCSP,
  })
}

/** Defines the immutable deny-by-default Electron web-preference contract. */
const requiredPreferences = {
  contextIsolation: true,
  nodeIntegration: false,
  nodeIntegrationInWorker: false,
  nodeIntegrationInSubFrames: false,
  sandbox: true,
  webSecurity: true,
  webviewTag: false,
  allowRunningInsecureContent: false,
  navigateOnDragDrop: false,
} as const

/**
 * Rejects input that violates the deny-by-default renderer security policy before any privileged action occurs.
 */
export function assertSecureWebPreferences(preferences: object, production: boolean): void {
  const values = preferences as Record<string, unknown>
  for (const [key, expected] of Object.entries(requiredPreferences)) {
    if (values[key] !== expected) throw new Error(`Insecure web preference: ${key}`)
  }
  if (production && values.devTools !== false) throw new Error('Insecure web preference: devTools')
}
