import type { AncestryBridge, DesktopTheme } from '../shared-contract/desktop'
import { parseTheme } from '../shared-contract/runtime'
import { failureFixture, successFixture } from './fixtures'

export function createMockAncestryBridge(mode: 'success' | 'failure' = 'success'): AncestryBridge {
  return Object.freeze({
    async startup() { return mode === 'success' ? successFixture : failureFixture },
    async setTheme(theme: DesktopTheme) { parseTheme(theme); return mode === 'success' ? successFixture : failureFixture },
  })
}
