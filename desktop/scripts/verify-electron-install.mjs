import { statSync } from 'node:fs'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)

function fail() {
  console.error(
    'DESKTOP_ELECTRON_INSTALL_INVALID: the locked Electron runtime is unavailable after rebuild',
  )
  process.exitCode = 2
}

try {
  const executable = require('electron')
  if (typeof executable !== 'string' || !statSync(executable).isFile()) {
    fail()
  } else {
    console.log('Locked Electron runtime installation verified.')
  }
} catch {
  fail()
}
