/** Verifies build inspection rejects unsafe content and resolves cross-platform paths. */
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { inspectBuild, resolveBuildOutputPath } from './verify-build.mjs'

test('build output path converts a Windows file URL without duplicating the drive prefix', () => {
  assert.equal(
    resolveBuildOutputPath(
      'file:///C:/a/AncestryLLM/AncestryLLM/desktop/scripts/verify-build.mjs',
      { windows: true },
    ),
    String.raw`C:\a\AncestryLLM\AncestryLLM\desktop\out`,
  )
})

test('build inspection rejects development copy, source maps, remote assets, credentials, and updater metadata', async () => {
  for (const [name, contents] of [
    ['renderer.js', 'const heading = "Component gallery"'],
    ['app.js.map', '{}'],
    ['app.js', 'fetch("https://remote.invalid/api")'],
    ['index.html', '<script src="https://remote.invalid/app.js"></script>'],
    ['credentials.txt', 'password=fake'],
    ['latest.yml', 'version: 1'],
  ]) {
    const root = await mkdtemp(join(tmpdir(), 'ancestryllm-build-'))
    await mkdir(join(root, 'out'))
    await writeFile(join(root, 'out', name), contents)
    await assert.rejects(inspectBuild(join(root, 'out')))
  }
})

test('production build inspection rejects fixture bridge and test-hook machinery', async () => {
  for (const contents of [
    'createMockAncestryBridge("success")',
    'process.env.ANCESTRYLLM_DESKTOP_FIXTURE',
    'process.env.ANCESTRYLLM_DESKTOP_SECURITY_E2E',
    'globalThis.__ancestryllmSecurityStateForTests = () => ({})',
  ]) {
    const root = await mkdtemp(join(tmpdir(), 'ancestryllm-build-'))
    await mkdir(join(root, 'out'))
    await writeFile(join(root, 'out', 'index.js'), contents)
    await assert.rejects(inspectBuild(join(root, 'out')))
  }
})

test('production build inspection rejects packaged file-grant verification selectors', async () => {
  for (const contents of [
    'process.env.ANCESTRYLLM_PACKAGED_FILE_GRANT_VERIFICATION',
    'process.env.ANCESTRYLLM_FILE_GRANT_OPEN_PATH',
    'process.env.ANCESTRYLLM_FILE_GRANT_SAVE_PATH',
  ]) {
    const root = await mkdtemp(join(tmpdir(), 'ancestryllm-build-'))
    await mkdir(join(root, 'out'))
    await writeFile(join(root, 'out', 'index.js'), contents)
    await assert.rejects(inspectBuild(join(root, 'out')))
  }
})

test('production build inspection rejects native storage verification selectors', async () => {
  for (const selector of [
    '--ancestryllm-linux-keyring-verification-root=/tmp/private',
    '--ancestryllm-macos-ephemeral-verification',
  ]) {
    const root = await mkdtemp(join(tmpdir(), 'ancestryllm-build-'))
    await mkdir(join(root, 'out'))
    await writeFile(join(root, 'out', 'index.js'), `const argument = '${selector}'`)
    await assert.rejects(inspectBuild(join(root, 'out')))
  }
})

test('packaged native-verification build permits only its dedicated selector', async () => {
  const root = await mkdtemp(join(tmpdir(), 'ancestryllm-build-'))
  await mkdir(join(root, 'out'))
  await writeFile(
    join(root, 'out', 'index.js'),
    "const arguments = ['--ancestryllm-linux-keyring-verification-root=/tmp/private', '--ancestryllm-macos-ephemeral-verification']",
  )
  await inspectBuild(join(root, 'out'), { allowPackagedNativeVerification: true })

  await writeFile(join(root, 'out', 'index.js'), 'process.env.ANCESTRYLLM_DESKTOP_FIXTURE')
  await assert.rejects(
    inspectBuild(join(root, 'out'), { allowPackagedNativeVerification: true }),
  )
})

test('packaged file-grant build permits only its dedicated verification selectors', async () => {
  const root = await mkdtemp(join(tmpdir(), 'ancestryllm-build-'))
  await mkdir(join(root, 'out'))
  await writeFile(
    join(root, 'out', 'index.js'),
    "process.env.ANCESTRYLLM_PACKAGED_FILE_GRANT_VERIFICATION; process.env.ANCESTRYLLM_FILE_GRANT_OPEN_PATH; process.env.ANCESTRYLLM_FILE_GRANT_SAVE_PATH; '--ancestryllm-linux-keyring-verification-root=/tmp/private'",
  )
  await inspectBuild(join(root, 'out'), {
    allowPackagedNativeVerification: true,
    allowPackagedFileGrants: true,
  })

  await assert.rejects(
    inspectBuild(join(root, 'out'), { allowPackagedFileGrants: true }),
  )

  await writeFile(join(root, 'out', 'index.js'), 'process.env.ANCESTRYLLM_DESKTOP_FIXTURE')
  await assert.rejects(inspectBuild(join(root, 'out'), {
    allowPackagedNativeVerification: true,
    allowPackagedFileGrants: true,
  }))
})
