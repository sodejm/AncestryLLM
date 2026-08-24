/** Owns the authoritative WebdriverIO sessions for source and packaged Electron tests. */
import '@wdio/electron-service'
import { isAbsolute, join, resolve } from 'node:path'
import { LINUX_KEYRING_VERIFICATION_SWITCH } from './src/main/sidecar-supervisor'

const mode = process.env.ANCESTRYLLM_WDIO_MODE
if (mode !== 'source' && mode !== 'packaged') {
  throw new Error('ANCESTRYLLM_WDIO_MODE must be source or packaged')
}

const packagedExecutable = process.env.ANCESTRYLLM_PACKAGED_EXECUTABLE
if (mode === 'packaged' && !packagedExecutable) {
  throw new Error('ANCESTRYLLM_PACKAGED_EXECUTABLE is required for packaged tests')
}

const userDataDirectory = process.env.ANCESTRYLLM_WDIO_USER_DATA
if (!userDataDirectory) {
  throw new Error('ANCESTRYLLM_WDIO_USER_DATA is required for isolated Electron tests')
}

const appArgs = [
  `--user-data-dir=${userDataDirectory}`,
  `--disk-cache-dir=${join(userDataDirectory, 'chromium-cache')}`,
  `--crash-dumps-dir=${join(userDataDirectory, 'crash-dumps')}`,
]
if (process.platform === 'darwin') appArgs.push('--use-mock-keychain')
if (process.platform === 'linux' && mode === 'packaged') {
  const nativeKeyringRoot = process.env.ANCESTRYLLM_NATIVE_KEYRING_ROOT
  if (!nativeKeyringRoot || !isAbsolute(nativeKeyringRoot)) {
    throw new Error('Packaged Linux verification requires an absolute native keyring root')
  }
  appArgs.push(`--${LINUX_KEYRING_VERIFICATION_SWITCH}=${nativeKeyringRoot}`)
}

const electronServiceOptions = mode === 'packaged'
  ? {
      appBinaryPath: packagedExecutable,
      appArgs,
    }
  : {
      appEntryPoint: resolve('out/main/index.js'),
      appArgs,
    }

/** Configures isolated source and packaged Electron sessions with service-managed automation. */
export const config = {
  runner: 'local',
  autoXvfb: true,
  specs: [],
  suites: {
    source: ['./e2e/shell.wdio.ts'],
    packaged: ['./e2e/packaged-shell.wdio.ts'],
  },
  maxInstances: 1,
  capabilities: [{
    browserName: 'electron',
    'wdio:electronServiceOptions': electronServiceOptions,
  }],
  beforeSession: () => {
    process.env.ANCESTRYLLM_WDIO_LAUNCH_STARTED_AT = String(Date.now())
  },
  services: ['electron'],
  framework: 'mocha',
  reporters: ['spec'],
  waitforTimeout: 15_000,
  connectionRetryTimeout: 120_000,
  connectionRetryCount: 2,
  mochaOpts: {
    ui: 'bdd',
    timeout: 300_000,
  },
}
