/** Composes the authenticated sidecar, local runtime, preferences, and desktop bridge. */
import { app } from 'electron'
import { join } from 'node:path'
import { createDesktopControlBridge } from './desktop-control'
import { createPackagedLocalRuntimeControl } from './local-runtime-control'
import type { MainDesktopBridge } from './ipc-handlers'
import { FilePreferencesStore } from './preferences-store'
import {
  createSidecarClient,
  requestSidecarRuntimeShutdown,
  type JobShutdownAction,
} from './sidecar-client'
import { SidecarIntegrityError, verifySidecarPayload } from './sidecar-integrity'
import {
  launchNativeSidecar,
  NATIVE_SIDECAR_SHUTDOWN_TIMEOUT_MS,
  probeNativeSidecar,
} from './sidecar-process'
import {
  resolveSidecarExecutable,
  resolveSidecarTargetRoot,
  SidecarSupervisor,
} from './sidecar-supervisor'
import type { RecordDesktopDiagnostic } from './structured-diagnostics'

/**
 * Returns the composed bridge plus the sidecar lifecycle hooks owned by the Electron main process.
 */
export interface RuntimeBridge {
  bridge: MainDesktopBridge
  supervisor?: SidecarSupervisor
  prepareJobShutdown?: (action: JobShutdownAction) => Promise<void>
}

/**
 * Supplies the local-runtime control port and optional stream/process adapters for bridge execution.
 */
export interface RuntimeBridgeOptions {
  linuxKeyringVerificationRoot?: string | undefined
  macosEphemeralWorkspaceVerification?: boolean | undefined
  diagnosticRunId?: string | undefined
  diagnosticDirectory?: string | undefined
  recordDiagnostic?: RecordDesktopDiagnostic | undefined
}

type OwnSidecarSupervisor = (
  supervisor: SidecarSupervisor,
  prepareJobShutdown: (action: JobShutdownAction) => Promise<void>,
) => void

/**
 * Reads one bounded stdin frame, validates the privileged request, and emits one newline-delimited JSON result.
 */
export async function startRuntimeBridge(
  onSupervisorOwned?: OwnSidecarSupervisor,
  options: RuntimeBridgeOptions = {},
): Promise<RuntimeBridge> {
  if (!app.isPackaged) {
    throw new Error('The production runtime bridge requires a packaged application.')
  }

  const preferences = new FilePreferencesStore(app.getPath('userData'))
  const target = `${process.platform}-${process.arch}`
  const supervisor = new SidecarSupervisor({
    appBuild: app.getVersion(),
    diagnosticDirectory: options.diagnosticDirectory
      ?? join(app.getPath('userData'), 'diagnostics'),
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
    requestShutdown: requestSidecarRuntimeShutdown,
    startupTimeoutMs: 10_000,
    shutdownTimeoutMs: NATIVE_SIDECAR_SHUTDOWN_TIMEOUT_MS,
    maxRestarts: 2,
    maxManualRetries: 1,
    linuxKeyringVerificationRoot: options.linuxKeyringVerificationRoot,
    ...(options.macosEphemeralWorkspaceVerification === undefined
      ? {}
      : { macosEphemeralWorkspaceVerification: options.macosEphemeralWorkspaceVerification }),
    ...(options.diagnosticRunId === undefined ? {} : { diagnosticRunId: options.diagnosticRunId }),
    ...(options.recordDiagnostic === undefined ? {} : { recordDiagnostic: options.recordDiagnostic }),
  })
  const sidecarClient = createSidecarClient({ session: () => supervisor.session() })
  const desktopControl = createDesktopControlBridge({
    appInfo: {
      applicationName: 'AncestryLLM',
      appVersion: app.getVersion(),
      buildChannel: 'packaged',
    },
    supervisor,
    sidecarClient,
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
  const prepareJobShutdown = async (action: JobShutdownAction): Promise<void> => {
    await sidecarClient.prepareJobShutdown(action)
  }

  onSupervisorOwned?.(supervisor, prepareJobShutdown)
  await supervisor.start().catch(() => undefined)
  return {
    bridge,
    supervisor,
    prepareJobShutdown,
  }
}
