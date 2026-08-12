import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const projectRoot = fileURLToPath(new URL('../..', import.meta.url))
const verifier = fileURLToPath(
  new URL('./verify-electron-install.mjs', import.meta.url),
)
function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: projectRoot,
    shell: false,
    stdio: 'inherit',
  })

  if (result.error !== undefined) {
    console.error(
      `DESKTOP_INSTALL_COMMAND_FAILED: ${command} could not be started`,
    )
    process.exit(2)
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 2)
  }
}

function runPnpm(args) {
  if (process.platform === 'win32') {
    const commandPrompt =
      process.env.ComSpec ?? process.env.COMSPEC ?? 'cmd.exe'
    run(commandPrompt, ['/d', '/s', '/c', 'pnpm.cmd', ...args])
    return
  }
  run('pnpm', args)
}

runPnpm(['--dir', 'desktop', 'install', '--frozen-lockfile'])
runPnpm(['--dir', 'desktop', 'rebuild', 'electron'])
run(process.execPath, [verifier])
