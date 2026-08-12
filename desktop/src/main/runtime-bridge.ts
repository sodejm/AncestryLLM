import { app } from 'electron'
import { createDesktopControlBridge } from './desktop-control'
import { createPackagedLocalRuntimeControl } from './local-runtime-control'
import type { MainDesktopBridge } from './ipc-handlers'
import { FilePreferencesStore } from './preferences-store'
import { createSidecarClient } from './sidecar-client'
import { SidecarIntegrityError, verifySidecarPayload } from './sidecar-integrity'
import { launchNativeSidecar, probeNativeSidecar } from './sidecar-process'
import {
  resolveSidecarExecutable,
  resolveSidecarTargetRoot,
  SidecarSupervisor,
} from './sidecar-supervisor'

export interface RuntimeBridge {
  bridge: MainDesktopBridge
  supervisor?: SidecarSupervisor
}

export async function startRuntimeBridge(): Promise<RuntimeBridge> {
  if (!app.isPackaged) {
    throw new Error('The production runtime bridge requires a packaged application.')
  }

  const preferences = new FilePreferencesStore(app.getPath('userData'))
  const target = `${process.platform}-${process.arch}`
  const supervisor = new SidecarSupervisor({
    appBuild: app.getVersion(),
    executablePath: resolveSidecarExecutable(process.resourcesPath, process.platform, process.arch),
    verify: async () => {
      if (__ANCESTRYLLM_SIDECAR_MANIFEST_SHA256__ === null) {
        throw new SidecarIntegrityError()
      }
      await verifySidecarPayload({
        targetRoot: resolveSidecarTargetRoot(
          process.resourcesPath,
          process.platform,
          process.arch,
        ),
        expectedManifestSha256: __ANCESTRYLLM_SIDECAR_MANIFEST_SHA256__,
        expectedTarget: target,
        appBuild: app.getVersion(),
      })
    },
    launch: launchNativeSidecar,
    probe: probeNativeSidecar,
    startupTimeoutMs: 10_000,
    maxRestarts: 2,
    maxManualRetries: 1,
  })
  const desktopControl = createDesktopControlBridge({
    appInfo: {
      applicationName: 'AncestryLLM',
      appVersion: app.getVersion(),
      buildChannel: 'packaged',
    },
    supervisor,
    sidecarClient: createSidecarClient({ session: () => supervisor.session() }),
    preferences,
  })
  const localRuntimeControl = createPackagedLocalRuntimeControl(
    process.resourcesPath,
    app.getPath('userData'),
  )
  const bridge: MainDesktopBridge = Object.freeze({
    ...desktopControl,
    ...localRuntimeControl,
  })

  await supervisor.start().catch(() => undefined)
  return { bridge, supervisor }
}
