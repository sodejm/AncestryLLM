import { app } from 'electron'
import type { AncestryBridge } from '../shared-contract/desktop'
import { createDesktopControlBridge } from './desktop-control'
import { FilePreferencesStore } from './preferences-store'
import { createSidecarCapabilitiesClient } from './sidecar-client'
import { launchNativeSidecar, probeNativeSidecar } from './sidecar-process'
import { resolveSidecarExecutable, SidecarSupervisor } from './sidecar-supervisor'

export interface RuntimeBridge {
  bridge: AncestryBridge
  supervisor?: SidecarSupervisor
}

export async function startRuntimeBridge(): Promise<RuntimeBridge> {
  if (!app.isPackaged) {
    throw new Error('The production runtime bridge requires a packaged application.')
  }

  const preferences = new FilePreferencesStore(app.getPath('userData'))
  const supervisor = new SidecarSupervisor({
    appBuild: app.getVersion(),
    executablePath: resolveSidecarExecutable(process.resourcesPath, process.platform, process.arch),
    launch: launchNativeSidecar,
    probe: probeNativeSidecar,
    startupTimeoutMs: 10_000,
    maxRestarts: 2,
    maxManualRetries: 1,
  })
  const bridge = createDesktopControlBridge({
    appInfo: {
      applicationName: 'AncestryLLM',
      appVersion: app.getVersion(),
      buildChannel: 'packaged',
    },
    supervisor,
    capabilitiesClient: createSidecarCapabilitiesClient({ session: () => supervisor.session() }),
    preferences,
  })

  await supervisor.start().catch(() => undefined)
  return { bridge, supervisor }
}
