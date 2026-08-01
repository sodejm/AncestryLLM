import { createMockAncestryBridge } from '../mock-bridge/desktop'
import type { AncestryBridge } from '../shared-contract/desktop'

interface FixtureRuntimeBridge {
  bridge: AncestryBridge
  supervisor?: never
}

export async function startRuntimeBridge(): Promise<FixtureRuntimeBridge> {
  const fixture = process.env.ANCESTRYLLM_DESKTOP_FIXTURE
  return {
    bridge: createMockAncestryBridge(
      fixture === 'degraded' || fixture === 'unavailable' ? fixture : 'success',
    ),
  }
}
