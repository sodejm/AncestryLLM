/** Exercises compatibility at the exact transitive paths used by desktop tooling. */
import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { createWriteStream } from 'node:fs'
import {
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  readlink,
  readdir,
  rm,
  writeFile,
} from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { finished } from 'node:stream/promises'
import test from 'node:test'

const desktopDir = fileURLToPath(new URL('..', import.meta.url))
const repositoryDir = dirname(desktopDir)
const pnpmModulesDir = join(desktopDir, 'node_modules', '.pnpm')
const packageJson = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'))
const wdioConfig = await readFile(new URL('../wdio.conf.ts', import.meta.url), 'utf8')

async function pnpmPackagePath(prefix, packagePath) {
  const matches = (await readdir(pnpmModulesDir)).filter((entry) => entry.startsWith(prefix))
  assert.equal(matches.length, 1, `expected one installed ${prefix} package, received ${matches}`)
  return join(pnpmModulesDir, matches[0], 'node_modules', packagePath)
}

async function writeZip(zipPath, entries) {
  const archiverPath = await pnpmPackagePath('archiver@8.0.0', 'archiver/index.js')
  const { ZipArchive } = await import(pathToFileURL(archiverPath).href)
  const archive = new ZipArchive()
  const output = createWriteStream(zipPath)
  const complete = finished(output)
  archive.pipe(output)
  for (const entry of entries) {
    if (entry.target) {
      archive.symlink(entry.name, entry.target, 0o777)
    } else {
      archive.append(Buffer.from(entry.contents), { name: entry.name, mode: 0o644 })
    }
  }
  await archive.finalize()
  await complete
}

async function loadPuppeteerExtractors() {
  const packageRoot = await pnpmPackagePath(
    '@puppeteer+browsers@2.13.2',
    '@puppeteer/browsers',
  )
  const esmPath = join(packageRoot, 'lib', 'esm', 'fileUtil.js')
  const cjsPath = join(packageRoot, 'lib', 'cjs', 'fileUtil.js')
  const esm = await import(pathToFileURL(esmPath).href)
  const cjs = createRequire(import.meta.url)(cjsPath)
  return [esm.unpackArchive, cjs.unpackArchive]
}

async function waitFor(predicate, description, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await predicate()) return
    await new Promise((resolve) => setTimeout(resolve, 50))
  }
  assert.fail(`timed out waiting for ${description}`)
}

test('Mocha 11 preserves watch-file globs through its Chokidar 4 adapter', { timeout: 20_000 }, async () => {
  const root = await mkdtemp(join(tmpdir(), 'ancestryllm-mocha-watch-'))
  const counterPath = join(root, 'runs.txt')
  const mochaCli = await pnpmPackagePath('mocha@11.8.0', 'mocha/bin/mocha.js')
  await mkdir(join(root, 'src', 'nested'), { recursive: true })
  await mkdir(join(root, 'test'), { recursive: true })
  await writeFile(
    join(root, 'test', 'watch.test.cjs'),
    "require('node:fs').appendFileSync(process.env.MOCHA_WATCH_COUNTER, 'run\\n'); describe('fixture', () => { it('passes', () => {}) })\n",
  )
  let output = ''
  const child = spawn(
    process.execPath,
    [mochaCli, '--watch', '--watch-files', 'src/**/*.js', '--reporter', 'dot', 'test/watch.test.cjs'],
    {
      cwd: root,
      env: { ...process.env, MOCHA_WATCH_COUNTER: counterPath },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  )
  child.stdout.on('data', (chunk) => {
    output += chunk.toString('utf8')
  })
  child.stderr.on('data', (chunk) => {
    output += chunk.toString('utf8')
  })

  try {
    await waitFor(
      () => output.includes('waiting for changes'),
      `Mocha watcher readiness; output was ${JSON.stringify(output)}`,
    )
    await writeFile(join(root, 'src', 'nested', 'change.js'), 'export const changed = true\n')
    await waitFor(
      async () => (await readFile(counterPath, 'utf8')).trim().split('\n').length >= 2,
      `a rerun from the nested watch glob; output was ${JSON.stringify(output)}`,
    )
  } finally {
    if (child.exitCode === null && child.signalCode === null) {
      child.kill('SIGINT')
      await new Promise((resolve) => child.once('exit', resolve))
    }
    await rm(root, { recursive: true, force: true })
  }
})

test('Mocha reporters remain compatible with the scoped diff 8 release', async () => {
  const basePath = await pnpmPackagePath('mocha@11.8.0', 'mocha/lib/reporters/base.js')
  const diffPackagePath = await pnpmPackagePath('diff@8.0.3', 'diff/package.json')
  const Base = createRequire(basePath)(basePath)
  const diffPackage = JSON.parse(await readFile(diffPackagePath, 'utf8'))
  const previous = { inlineDiffs: Base.inlineDiffs, useColors: Base.useColors }
  Base.useColors = false

  try {
    Base.inlineDiffs = false
    const unified = Base.generateDiff('first\nold\n', 'first\nnew\n')
    assert.match(unified, /\+ expected - actual/u)
    assert.match(unified, /-old/u)
    assert.match(unified, /\+new/u)

    Base.inlineDiffs = true
    const inline = Base.generateDiff('first old value', 'first new value')
    assert.match(inline, /actual/u)
    assert.match(inline, /expected/u)
    assert.match(inline, /old/u)
    assert.match(inline, /new/u)
    assert.equal(diffPackage.version, '8.0.3')
  } finally {
    Object.assign(Base, previous)
  }
})

test('WebdriverIO suites remain explicit after the Mocha parent upgrade', () => {
  assert.doesNotMatch(wdioConfig, /^\s*watch\s*:/mu)
  assert.match(wdioConfig, /^\s*specs:\s*\[\],$/mu)
  assert.match(wdioConfig, /^\s*source:\s*\['\.\/e2e\/shell\.wdio\.ts'\],$/mu)
  assert.match(wdioConfig, /^\s*packaged:\s*\['\.\/e2e\/packaged-shell\.wdio\.ts'\],$/mu)
})

test('packaging entry points fail closed for every nonempty legacy proxy switch', () => {
  for (const script of ['run-electron-builder.mjs', 'install-locked.mjs']) {
    for (const value of ['true', ' ']) {
      const result = spawnSync(process.execPath, [join(desktopDir, 'scripts', script), '--version'], {
        cwd: repositoryDir,
        env: { ...process.env, ELECTRON_GET_USE_PROXY: value },
        encoding: 'utf8',
      })
      assert.equal(result.status, 2, `${script} accepted ${JSON.stringify(value)}: ${result.stderr}`)
      assert.match(result.stderr, /ELECTRON_GET_USE_PROXY is unsupported/u)
    }
  }
})

test('packaging commands cannot bypass the guarded Electron builder entry point', async () => {
  assert.equal(
    packageJson.scripts.package,
    'node scripts/verify-sidecar.mjs && pnpm build && node scripts/run-electron-builder.mjs --dir',
  )
  const workflows = [
    join(repositoryDir, '.github', 'workflows', 'desktop-sidecar.yml'),
    join(repositoryDir, '.github', 'workflows', 'release.yml'),
  ]
  for (const workflow of workflows) {
    const contents = await readFile(workflow, 'utf8')
    assert.match(contents, /node desktop\/scripts\/run-electron-builder\.mjs/u)
    assert.doesNotMatch(
      contents,
      /(?:^|\s)(?:pnpm(?:\s+exec)?\s+|npx\s+)?electron-builder(?=\s)/mu,
    )
    assert.doesNotMatch(contents, /node_modules\/\.bin\/electron-builder/u)
  }
})

test('the guarded builder entry point resolves and runs the frozen CLI', () => {
  const env = { ...process.env }
  delete env.ELECTRON_GET_USE_PROXY
  const result = spawnSync(
    process.execPath,
    [join(desktopDir, 'scripts', 'run-electron-builder.mjs'), '--version'],
    { cwd: repositoryDir, env, encoding: 'utf8' },
  )
  assert.equal(result.status, 0, result.stderr)
  assert.match(result.stdout, /26\.15\.7/u)
})

test('get-uri FTP adapter remains compatible with scoped basic-ftp 6', async () => {
  const ftpModulePath = await pnpmPackagePath('get-uri@6.0.5', 'get-uri/dist/ftp.js')
  const ftpRequire = createRequire(ftpModulePath)
  const { Client } = ftpRequire('basic-ftp')
  const calls = []
  const original = {
    access: Client.prototype.access,
    close: Client.prototype.close,
    downloadTo: Client.prototype.downloadTo,
    lastMod: Client.prototype.lastMod,
  }
  const modifiedAt = new Date('2026-10-10T12:00:00Z')
  Client.prototype.access = async (options) => calls.push(['access', options])
  Client.prototype.lastMod = async (filepath) => {
    calls.push(['lastMod', filepath])
    return modifiedAt
  }
  Client.prototype.downloadTo = async (stream, filepath) => {
    calls.push(['downloadTo', filepath])
    stream.end('fixture-payload')
    return { message: 'transfer complete' }
  }
  Client.prototype.close = () => calls.push(['close'])

  try {
    const { ftp } = ftpRequire(ftpModulePath)
    const fixtureUrl = new URL('ftp://example.test:2121/tree.ged')
    fixtureUrl.username = 'fixture-user'
    fixtureUrl.password = 'fixture-pass'
    const stream = await ftp(fixtureUrl)
    let contents = ''
    for await (const chunk of stream) contents += chunk.toString('utf8')
    assert.equal(contents, 'fixture-payload')
    assert.equal(+stream.lastModified, +modifiedAt)
    assert.deepEqual(calls, [
      [
        'access',
        {
          host: 'example.test',
          port: 2121,
          user: 'fixture-user',
          password: 'fixture-pass',
        },
      ],
      ['lastMod', '/tree.ged'],
      ['downloadTo', '/tree.ged'],
      ['close'],
    ])
  } finally {
    Object.assign(Client.prototype, original)
    delete ftpRequire.cache[ftpModulePath]
  }
})

test(
  'Puppeteer CJS and ESM ZIP call sites preserve in-archive symlinks with the Electron extractor',
  { skip: process.platform === 'win32' ? 'Windows symlink creation requires host policy' : false },
  async () => {
    const root = await mkdtemp(join(tmpdir(), 'ancestryllm-extract-zip-'))
    try {
      const archivePath = join(root, 'fixture.zip')
      await writeZip(archivePath, [
        { name: 'target.txt', contents: 'fixture-payload' },
        { name: 'link.txt', target: 'target.txt' },
      ])
      const extractors = await loadPuppeteerExtractors()
      for (const [index, unpackArchive] of extractors.entries()) {
        const outputDir = join(root, `output-${index}`)
        await unpackArchive(archivePath, outputDir)
        const linkPath = join(outputDir, 'link.txt')
        assert.equal((await lstat(linkPath)).isSymbolicLink(), true)
        assert.equal(await readlink(linkPath), 'target.txt')
        assert.equal(await readFile(linkPath, 'utf8'), 'fixture-payload')
      }
    } finally {
      await rm(root, { recursive: true, force: true })
    }
  },
)

test(
  'Puppeteer ZIP extraction rejects a symlink traversal before writing outside its output',
  { skip: process.platform === 'win32' ? 'Windows symlink creation requires host policy' : false },
  async () => {
    const root = await mkdtemp(join(tmpdir(), 'ancestryllm-extract-zip-traversal-'))
    try {
      const archivePath = join(root, 'traversal.zip')
      await writeZip(archivePath, [
        { name: 'pivot', target: '..' },
        { name: 'pivot/escaped.txt', contents: 'must-not-escape' },
      ])
      const [unpackArchive] = await loadPuppeteerExtractors()
      await assert.rejects(unpackArchive(archivePath, join(root, 'output')))
      await assert.rejects(readFile(join(root, 'escaped.txt'), 'utf8'), { code: 'ENOENT' })
    } finally {
      await rm(root, { recursive: true, force: true })
    }
  },
)
