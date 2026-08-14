/** Verifies packaged renderer navigation cannot be redirected to untrusted origins. */
import { describe, expect, it } from 'vitest'
import { isTrustedRendererUrl, resolveRendererTarget } from './renderer-location'

const rendererPath = '/application/out/renderer/index.html'

describe('renderer location policy', () => {
  it('ignores a remote development URL in a packaged application', () => {
    expect(resolveRendererTarget({
      developmentUrl: 'https://attacker.invalid/app',
      isPackaged: true,
      rendererPath,
    })).toEqual({ kind: 'url', value: 'app://bundle/index.html' })

    expect(isTrustedRendererUrl({
      developmentUrl: 'https://attacker.invalid/app',
      isPackaged: true,
      rendererPath,
      senderUrl: 'https://attacker.invalid/app',
    })).toBe(false)
  })

  it('retains the development server workflow for an unpackaged application', () => {
    expect(resolveRendererTarget({
      developmentUrl: 'http://localhost:5173',
      isPackaged: false,
      rendererPath,
    })).toEqual({ kind: 'url', value: 'http://localhost:5173' })

    expect(isTrustedRendererUrl({
      developmentUrl: 'http://localhost:5173',
      isPackaged: false,
      rendererPath,
      senderUrl: 'http://localhost:5173/settings#theme',
    })).toBe(true)
  })

  it('uses the production app route when unpackaged without a development server', () => {
    expect(resolveRendererTarget({
      developmentUrl: undefined,
      isPackaged: false,
      rendererPath,
    })).toEqual({ kind: 'url', value: 'app://bundle/index.html' })

    expect(isTrustedRendererUrl({
      developmentUrl: undefined,
      isPackaged: false,
      rendererPath,
      senderUrl: 'app://bundle/index.html#home',
    })).toBe(true)
    expect(isTrustedRendererUrl({
      developmentUrl: undefined,
      isPackaged: false,
      rendererPath,
      senderUrl: 'https://attacker.invalid/app',
    })).toBe(false)
  })

  it('trusts only the fixed app origin and entry document in packaged mode', () => {
    expect(isTrustedRendererUrl({
      developmentUrl: undefined,
      isPackaged: true,
      rendererPath,
      senderUrl: 'app://bundle/index.html#home',
    })).toBe(true)
    expect(isTrustedRendererUrl({
      developmentUrl: undefined,
      isPackaged: true,
      rendererPath,
      senderUrl: 'file:///application/out/renderer/index.html#home',
    })).toBe(false)
    expect(isTrustedRendererUrl({
      developmentUrl: undefined,
      isPackaged: true,
      rendererPath,
      senderUrl: 'app://bundle/other.html',
    })).toBe(false)
    expect(isTrustedRendererUrl({
      developmentUrl: undefined,
      isPackaged: true,
      rendererPath,
      senderUrl: 'not a url',
    })).toBe(false)
  })
})
