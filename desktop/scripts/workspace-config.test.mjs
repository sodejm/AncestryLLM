import assert from 'node:assert/strict'
import { access, readFile } from 'node:fs/promises'
import test from 'node:test'

const workspaceUrl = new URL('../pnpm-workspace.yaml', import.meta.url)
const lockfileUrl = new URL('../pnpm-lock.yaml', import.meta.url)
const npmrcUrl = new URL('../.npmrc', import.meta.url)
const packageUrl = new URL('../package.json', import.meta.url)

test('pnpm 11 controls live in the supported workspace config and lockfile', async () => {
  const workspace = await readFile(workspaceUrl, 'utf8')
  const lockfile = await readFile(lockfileUrl, 'utf8')
  const packageJson = JSON.parse(await readFile(packageUrl, 'utf8'))
  const expected = {
    autoInstallPeers: 'false',
    enablePrePostScripts: 'false',
    engineStrict: 'true',
    ignoreScripts: 'false',
    saveExact: 'true',
    sharedWorkspaceLockfile: 'false',
  }

  for (const [name, value] of Object.entries(expected)) {
    assert.match(workspace, new RegExp(`^${name}: ${value}$`, 'm'))
  }
  assert.match(lockfile, /^settings:\n {2}autoInstallPeers: false$/m)
  assert.equal(packageJson.devDependencies['@testing-library/dom'], '10.4.1')
  assert.equal(packageJson.devDependencies['electron-builder-squirrel-windows'], '26.15.3')
  await assert.rejects(access(npmrcUrl), { code: 'ENOENT' })
})
