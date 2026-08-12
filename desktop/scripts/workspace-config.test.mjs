import assert from 'node:assert/strict'
import { access, readFile } from 'node:fs/promises'
import test from 'node:test'

const workspaceUrl = new URL('../pnpm-workspace.yaml', import.meta.url)
const lockfileUrl = new URL('../pnpm-lock.yaml', import.meta.url)
const npmrcUrl = new URL('../.npmrc', import.meta.url)
const packageUrl = new URL('../package.json', import.meta.url)
const builderConfigUrl = new URL('../electron-builder.yml', import.meta.url)
const releaseBuilderConfigUrl = new URL('../electron-builder.release.yml', import.meta.url)

const runtimePolicyResource = {
  from: 'resources/macos-arm64-runtime-policy-v1.json',
  to: 'runtime-policy/macos-arm64-runtime-policy-v1.json',
}

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
  assert.match(workspace, /^overrides:\n {2}fast-uri: 3\.1\.5$/m)
  assert.match(lockfile, /^overrides:\n {2}fast-uri: 3\.1\.5$/m)
  assert.match(lockfile, /^ {2}fast-uri@3\.1\.5:$/m)
  assert.doesNotMatch(lockfile, /^ {2}fast-uri@3\.1\.4:$/m)
  assert.equal(packageJson.devDependencies['@testing-library/dom'], '10.4.1')
  assert.equal(packageJson.devDependencies['electron-builder'], '26.15.7')
  assert.equal(packageJson.devDependencies['electron-builder-squirrel-windows'], '26.15.7')
  await assert.rejects(access(npmrcUrl), { code: 'ENOENT' })
})

test('every standalone builder packages the reviewed local-runtime policy', async () => {
  const packageJson = JSON.parse(await readFile(packageUrl, 'utf8'))

  assert.deepEqual(
    packageJson.build.extraResources.filter((resource) => resource.to === runtimePolicyResource.to),
    [runtimePolicyResource],
  )

  const expectedYaml = [
    `  - from: ${runtimePolicyResource.from}`,
    `    to: ${runtimePolicyResource.to}`,
  ].join('\n')
  for (const configUrl of [builderConfigUrl, releaseBuilderConfigUrl]) {
    const config = await readFile(configUrl, 'utf8')
    assert.equal(config.split(expectedYaml).length - 1, 1)
  }
})
