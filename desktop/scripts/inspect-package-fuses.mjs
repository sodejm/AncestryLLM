import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { createHash } from 'node:crypto'
import { open, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { promisify } from 'node:util'
import { fileURLToPath } from 'node:url'
import { FuseState, FuseV1Options, getCurrentFuseWire } from '@electron/fuses'
import { discoverPackage } from './package-paths.mjs'

const execFileAsync = promisify(execFile)
const releaseRoot = fileURLToPath(new URL('../release/', import.meta.url))

const expectedFuses = [
  ['RunAsNode', FuseV1Options.RunAsNode, FuseState.DISABLE],
  ['EnableCookieEncryption', FuseV1Options.EnableCookieEncryption, FuseState.ENABLE],
  ['EnableNodeOptionsEnvironmentVariable', FuseV1Options.EnableNodeOptionsEnvironmentVariable, FuseState.DISABLE],
  ['EnableNodeCliInspectArguments', FuseV1Options.EnableNodeCliInspectArguments, FuseState.DISABLE],
  ['EnableEmbeddedAsarIntegrityValidation', FuseV1Options.EnableEmbeddedAsarIntegrityValidation, FuseState.ENABLE],
  ['OnlyLoadAppFromAsar', FuseV1Options.OnlyLoadAppFromAsar, FuseState.ENABLE],
  ['LoadBrowserProcessSpecificV8Snapshot', FuseV1Options.LoadBrowserProcessSpecificV8Snapshot, FuseState.DISABLE],
  ['GrantFileProtocolExtraPrivileges', FuseV1Options.GrantFileProtocolExtraPrivileges, FuseState.DISABLE],
]

const fuseStateNames = new Map([
  [FuseState.DISABLE, 'disabled'],
  [FuseState.ENABLE, 'enabled'],
  [FuseState.REMOVED, 'removed'],
  [FuseState.INHERIT, 'inherited'],
])

function fuseStateName(state) {
  const name = fuseStateNames.get(state)
  assert.ok(name, `Unknown Electron fuse state: ${state}`)
  return name
}

async function readExactly(handle, buffer, position, errorMessage) {
  let offset = 0
  while (offset < buffer.length) {
    const { bytesRead } = await handle.read(
      buffer,
      offset,
      buffer.length - offset,
      position + offset,
    )
    if (bytesRead === 0) break
    offset += bytesRead
  }
  assert.equal(offset, buffer.length, errorMessage)
}

async function readAsarHeaderHash(asarPath) {
  const handle = await open(asarPath, 'r')
  try {
    const fileSize = (await handle.stat()).size
    const sizeBuffer = Buffer.alloc(8)
    await readExactly(handle, sizeBuffer, 0, 'Unable to read packaged app.asar header size')

    const headerSize = sizeBuffer.readUInt32LE(4)
    assert.ok(
      headerSize >= 8 && headerSize <= fileSize - sizeBuffer.length,
      'Packaged app.asar has an invalid header size',
    )

    const headerBuffer = Buffer.alloc(headerSize)
    await readExactly(handle, headerBuffer, sizeBuffer.length, 'Unable to read packaged app.asar header')

    const headerStringSize = headerBuffer.readUInt32LE(4)
    assert.ok(
      headerStringSize > 0 && headerStringSize <= headerSize - 8,
      'Packaged app.asar has an invalid header string size',
    )
    return createHash('sha256')
      .update(headerBuffer.subarray(8, 8 + headerStringSize))
      .digest('hex')
  } finally {
    await handle.close()
  }
}

export function asarIntegrityReport(platform, plist, headerHash) {
  assert.ok(
    platform === 'darwin' || platform === 'win32' || platform === 'linux',
    `Unsupported package platform: ${platform}`,
  )

  const scope = 'ElectronAsarIntegrity Info.plist metadata for Resources/app.asar'
  if (platform !== 'darwin') {
    return {
      status: 'not-applicable',
      scope,
      reason: 'ElectronAsarIntegrity Info.plist metadata exists only in macOS application bundles; app.asar presence and the embedded-integrity fuse were verified separately.',
    }
  }

  const metadata = plist?.ElectronAsarIntegrity?.['Resources/app.asar']
  assert.equal(metadata?.algorithm, 'SHA256', 'Packaged app.asar integrity metadata must use SHA256')
  assert.match(
    metadata?.hash ?? '',
    /^[a-f0-9]{64}$/i,
    'Packaged app.asar integrity metadata must contain a SHA256 hash',
  )
  assert.equal(
    metadata.hash.toLowerCase(),
    headerHash,
    'Packaged app.asar integrity metadata does not match the ASAR header',
  )
  return {
    status: 'verified',
    scope,
    algorithm: metadata.algorithm,
    hash: metadata.hash.toLowerCase(),
  }
}

export function parseArguments(argv) {
  if (argv.length === 0) return {}
  assert.equal(argv.length, 2, 'Usage: node scripts/inspect-package-fuses.mjs [--output <path>]')
  assert.equal(argv[0], '--output', 'Usage: node scripts/inspect-package-fuses.mjs [--output <path>]')
  assert.ok(argv[1], 'The --output option requires a path')
  return { outputPath: argv[1] }
}

export async function writeInspectionReport(outputPath, report) {
  await writeFile(outputPath, `${JSON.stringify(report, null, 2)}\n`, {
    encoding: 'utf8',
    flag: 'wx',
    mode: 0o600,
  })
}

export function formatInspectionSummary(report) {
  if (report.asar.integrity.status === 'verified') {
    return `Verified app.asar presence, ${report.fuses.count} packaged Electron fuse states, and macOS ElectronAsarIntegrity Info.plist metadata.`
  }
  return `Verified app.asar presence and ${report.fuses.count} packaged Electron fuse states; ElectronAsarIntegrity Info.plist metadata verification is not applicable on ${report.platform}.`
}

export async function inspectPackage({ root = releaseRoot, platform = process.platform } = {}) {
  const { executable, resources } = await discoverPackage(root, platform)
  const fuses = await getCurrentFuseWire(executable)
  const fuseItems = expectedFuses.map(([name, option, expected]) => {
    const actual = fuses[option]
    assert.equal(actual, expected, `Unexpected packaged fuse state for option ${name}`)
    return {
      name,
      expected: fuseStateName(expected),
      actual: fuseStateName(actual),
      status: 'verified',
    }
  })

  const asarPath = join(resources, 'app.asar')
  let integrity
  if (platform === 'darwin') {
    const infoPlist = join(resources, '..', 'Info.plist')
    const { stdout } = await execFileAsync('/usr/bin/plutil', ['-convert', 'json', '-o', '-', infoPlist])
    integrity = asarIntegrityReport(
      platform,
      JSON.parse(stdout),
      await readAsarHeaderHash(asarPath),
    )
  } else {
    integrity = asarIntegrityReport(platform)
  }

  return {
    schemaVersion: 1,
    kind: 'ancestryllm-desktop-package-security-inspection',
    platform,
    package: {
      executable,
      application: platform === 'darwin' ? dirname(dirname(resources)) : dirname(executable),
      resources,
    },
    fuses: {
      status: 'verified',
      count: fuseItems.length,
      items: fuseItems,
    },
    asar: {
      path: asarPath,
      presence: { status: 'verified' },
      integrity,
    },
  }
}

async function main(argv) {
  const { outputPath } = parseArguments(argv)
  const report = await inspectPackage()
  if (outputPath) await writeInspectionReport(outputPath, report)
  console.log(formatInspectionSummary(report))
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main(process.argv.slice(2))
}
