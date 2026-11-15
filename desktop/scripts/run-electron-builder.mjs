/** Runs the pinned Electron builder after enforcing supported download behavior. */
import { spawn } from 'node:child_process'
import { createRequire } from 'node:module'
import process from 'node:process'

const legacyProxySwitch = process.env.ELECTRON_GET_USE_PROXY

if (legacyProxySwitch !== undefined && legacyProxySwitch !== '') {
  console.error(
    'ELECTRON_GET_USE_PROXY is unsupported because its optional legacy proxy agent has no patched release; unset it before packaging.',
  )
  process.exitCode = 2
} else {
  const require = createRequire(import.meta.url)
  const builderCli = require.resolve('electron-builder/cli.js')
  const child = spawn(process.execPath, [builderCli, ...process.argv.slice(2)], {
    cwd: new URL('..', import.meta.url),
    env: process.env,
    stdio: 'inherit',
  })
  child.on('error', (error) => {
    console.error(`Unable to start electron-builder: ${error.message}`)
    process.exitCode = 1
  })
  child.on('exit', (code, signal) => {
    if (signal !== null) {
      process.kill(process.pid, signal)
      return
    }
    process.exitCode = code ?? 1
  })
}
