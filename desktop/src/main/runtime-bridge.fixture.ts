import { createMockAncestryBridge } from '../mock-bridge/desktop'
import type { MainDesktopBridge } from './ipc-handlers'

interface FixtureRuntimeBridge {
  bridge: MainDesktopBridge
  supervisor?: never
}

export async function startRuntimeBridge(): Promise<FixtureRuntimeBridge> {
  const fixture = process.env.ANCESTRYLLM_DESKTOP_FIXTURE
  return {
    bridge: createMockAncestryBridge(
      fixture === 'degraded' || fixture === 'unavailable' ? fixture : 'success',
    ) as MainDesktopBridge,
  }
}
