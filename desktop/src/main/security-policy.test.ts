/** Tests the production renderer protocol, CSP, and BrowserWindow security preference policy. */
import { describe, expect, it, vi } from 'vitest'
import {
  APP_ASSET_MANIFEST,
  APP_ENTRY_URL,
  APP_SCHEME_PRIVILEGES,
  PRODUCTION_CSP,
  assertSecureWebPreferences,
  createAppProtocolHandler,
  createRuntimeSecurityState,
  secureWebPreferences,
} from './security-policy'

describe('production renderer security policy', () => {
  it('uses only the minimum custom-scheme privileges and a network-denying CSP', () => {
    expect(APP_ENTRY_URL).toBe('app://bundle/index.html')
    expect(APP_SCHEME_PRIVILEGES).toEqual({
      standard: true,
      secure: true,
      supportFetchAPI: false,
      bypassCSP: false,
      allowServiceWorkers: false,
      corsEnabled: false,
      stream: false,
      codeCache: false,
    })
    expect(PRODUCTION_CSP).toBe("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; font-src 'self'; connect-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'")
  })

  it('fixes every production route and MIME type in an exact manifest', () => {
    expect(APP_ASSET_MANIFEST).toEqual({
      '/index.html': { file: 'index.html', mime: 'text/html; charset=utf-8' },
      '/assets/index.js': { file: 'assets/index.js', mime: 'text/javascript; charset=utf-8' },
      '/assets/index.css': { file: 'assets/index.css', mime: 'text/css; charset=utf-8' },
    })
  })

  it('serves only fixed manifest assets with CSP and nosniff headers', async () => {
    const readAsset = vi.fn(async (file: string) => new TextEncoder().encode(file))
    const handler = createAppProtocolHandler(readAsset)
    const response = await handler(new Request(APP_ENTRY_URL))

    expect(response.status).toBe(200)
    expect(response.headers.get('content-type')).toBe('text/html; charset=utf-8')
    expect(response.headers.get('content-security-policy')).toBe(PRODUCTION_CSP)
    expect(response.headers.get('x-content-type-options')).toBe('nosniff')
    expect(await response.text()).toBe('index.html')
    expect(readAsset).toHaveBeenCalledWith('index.html')
  })

  it.each([
    'app://bundle/%2e%2e/secret.txt',
    'app://bundle/%252e%252e/secret.txt',
    'app://bundle/assets/%2e%2e/index.html',
    'app://bundle/assets/unknown.exe',
    'app://other/index.html',
    'app://bundle/index.html?override=assets/index.js',
    'app://bundle/index.html#assets/index.js',
  ])('fails closed for unknown or encoded paths: %s', async (url) => {
    const readAsset = vi.fn(async () => new Uint8Array())
    const response = await createAppProtocolHandler(readAsset)({ method: 'GET', url })

    expect(response.status).toBe(404)
    expect(readAsset).not.toHaveBeenCalled()
  })

  it('rejects methods other than GET and HEAD', async () => {
    const readAsset = vi.fn(async () => new Uint8Array())
    const response = await createAppProtocolHandler(readAsset)(new Request(APP_ENTRY_URL, { method: 'POST' }))
    expect(response.status).toBe(405)
    expect(readAsset).not.toHaveBeenCalled()
  })

  it('creates production preferences that deny Node, webviews, and devtools', () => {
    const preferences = secureWebPreferences('/application/preload.cjs', true)
    expect(preferences).toMatchObject({
      preload: '/application/preload.cjs',
      contextIsolation: true,
      nodeIntegration: false,
      nodeIntegrationInWorker: false,
      nodeIntegrationInSubFrames: false,
      sandbox: true,
      webSecurity: true,
      webviewTag: false,
      devTools: false,
      allowRunningInsecureContent: false,
      navigateOnDragDrop: false,
    })
    expect(() => assertSecureWebPreferences(preferences, true)).not.toThrow()
  })

  it('reports the complete main-process production policy without widening the renderer bridge', () => {
    expect(createRuntimeSecurityState(APP_ENTRY_URL, secureWebPreferences('/application/preload.cjs', true), true)).toEqual({
      mode: 'production',
      rendererUrl: APP_ENTRY_URL,
      contentSecurityPolicy: PRODUCTION_CSP,
      contextIsolation: true,
      nodeIntegration: false,
      nodeIntegrationInWorker: false,
      nodeIntegrationInSubFrames: false,
      sandbox: true,
      webSecurity: true,
      webviewTag: false,
      devTools: false,
      permissionsDenied: true,
      navigationDenied: true,
      childWindowsDenied: true,
      downloadsDenied: true,
      serviceWorkersAllowed: false,
      bypassCsp: false,
    })
  })

  it.each([
    ['contextIsolation', false],
    ['nodeIntegration', true],
    ['nodeIntegrationInWorker', true],
    ['nodeIntegrationInSubFrames', true],
    ['sandbox', false],
    ['webSecurity', false],
    ['webviewTag', true],
    ['devTools', true],
    ['allowRunningInsecureContent', true],
    ['navigateOnDragDrop', true],
  ] as const)('rejects a future production window that weakens %s', (key, value) => {
    const preferences = { ...secureWebPreferences('/application/preload.cjs', true), [key]: value }
    expect(() => assertSecureWebPreferences(preferences, true)).toThrow(`Insecure web preference: ${key}`)
  })
})
