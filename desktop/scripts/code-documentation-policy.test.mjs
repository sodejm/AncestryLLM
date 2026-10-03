/** Verifies exported desktop declarations require meaningful, valid documentation. */
import assert from 'node:assert/strict'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import test from 'node:test'

const scriptsDirectory = dirname(fileURLToPath(import.meta.url))
const desktopDirectory = dirname(scriptsDirectory)
const checkerPath = join(scriptsDirectory, 'check-code-documentation.mjs')
const eslintPath = join(desktopDirectory, 'node_modules', '.bin', 'eslint')
const fixtureDirectory = join(scriptsDirectory, 'fixtures', 'code-documentation')

async function runChecker(fixtureName, targetName) {
  const root = await mkdtemp(join(tmpdir(), 'ancestryllm-code-docs-'))
  try {
    const fixture = await readFile(join(fixtureDirectory, fixtureName), 'utf8')
    const targetPath = join(root, targetName)
    await mkdir(dirname(targetPath), { recursive: true })
    await writeFile(targetPath, fixture)
    return spawnSync(process.execPath, [checkerPath, '--root', root], {
      cwd: desktopDirectory,
      encoding: 'utf8',
    })
  } finally {
    await rm(root, { force: true, recursive: true })
  }
}

test('documented TypeScript, React, hook, declaration, license, and JavaScript exports pass', async () => {
  for (const [fixtureName, targetName] of [
    ['documented.ts.txt', 'documented.ts'],
    ['documented.d.ts.txt', 'documented.d.ts'],
    ['documented-license.ts.txt', 'documented-license.ts'],
    ['documented-exports.mjs.txt', 'documented-exports.mjs'],
  ]) {
    const result = await runChecker(fixtureName, targetName)
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`)
    assert.match(result.stdout, /desktop_code_documentation: all checks passed/u)
  }
})

test('an undocumented exported declaration fails with a stable diagnostic', async () => {
  const result = await runChecker('missing-export.ts.txt', 'missing-export.ts')

  assert.equal(result.status, 1, `${result.stdout}\n${result.stderr}`)
  assert.match(
    result.stdout,
    /^missing-export\.ts:missing-export-documentation:undocumentedBoundary$/mu,
  )
})

test('undocumented declaration-file and React exports fail closed', async () => {
  for (const [fixtureName, targetName, exportName] of [
    ['missing-export.ts.txt', 'missing-export.d.ts', 'undocumentedBoundary'],
    ['missing-react.tsx.txt', 'missing-react.tsx', 'MissingComponent'],
  ]) {
    const result = await runChecker(fixtureName, targetName)
    assert.equal(result.status, 1, `${result.stdout}\n${result.stderr}`)
    assert.match(
      result.stdout,
      new RegExp(`^${targetName.replaceAll('.', '\\.')}:missing-export-documentation:${exportName}$`, 'mu'),
    )
  }
})

test('placeholder export documentation fails closed', async () => {
  const result = await runChecker('placeholder-export.ts.txt', 'placeholder-export.ts')

  assert.equal(result.status, 1, `${result.stdout}\n${result.stderr}`)
  assert.match(
    result.stdout,
    /^placeholder-export\.ts:placeholder-export-documentation:placeholderDocumentation$/mu,
  )
})

test('an undocumented security-sensitive internal boundary fails closed', async () => {
  const result = await runChecker(
    'missing-security-boundary.ts.txt',
    'src/main/security-policy.ts',
  )

  assert.equal(result.status, 1, `${result.stdout}\n${result.stderr}`)
  assert.match(
    result.stdout,
    /^src\/main\/security-policy\.ts:missing-security-boundary-documentation:responseHeaders$/mu,
  )
})

test('reviewed internal security-boundary declarations and policy names stay synchronized', async () => {
  const documented = await runChecker(
    'documented-security-boundary.ts.txt',
    'src/main/security-policy.ts',
  )
  assert.equal(documented.status, 0, `${documented.stdout}\n${documented.stderr}`)

  const stale = await runChecker(
    'stale-security-boundary.ts.txt',
    'src/main/security-policy.ts',
  )
  assert.equal(stale.status, 1, `${stale.stdout}\n${stale.stderr}`)
  assert.match(
    stale.stdout,
    /^src\/main\/security-policy\.ts:security-boundary-declaration-missing:requiredPreferences$/mu,
  )
})

test('test declarations remain exempt from exported declaration coverage', async () => {
  const result = await runChecker('missing-export.ts.txt', 'missing-export.test.ts')

  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`)
})

test('filesystem failures return a stable coded error without local path disclosure', () => {
  const missingRoot = join(tmpdir(), `ancestryllm-code-docs-missing-${process.pid}`)
  const result = spawnSync(process.execPath, [checkerPath, '--root', missingRoot], {
    cwd: desktopDirectory,
    encoding: 'utf8',
  })

  assert.equal(result.status, 2, `${result.stdout}\n${result.stderr}`)
  assert.equal(result.stderr, 'desktop_code_documentation: filesystem-error\n')
  assert.doesNotMatch(result.stderr, new RegExp(missingRoot, 'u'))
})

test('ESLint rejects malformed JSDoc syntax', async () => {
  const fixture = await readFile(join(fixtureDirectory, 'malformed-jsdoc.ts.txt'), 'utf8')
  const result = spawnSync(
    eslintPath,
    ['--stdin', '--stdin-filename', 'src/malformed-jsdoc.ts', '--max-warnings=0'],
    { cwd: desktopDirectory, encoding: 'utf8', input: fixture },
  )

  assert.equal(result.status, 1, `${result.stdout}\n${result.stderr}`)
  assert.match(`${result.stdout}\n${result.stderr}`, /jsdoc\/check-syntax/u)
})

test('workspace pins and invokes the reviewed desktop documentation policy', async () => {
  const packageJson = JSON.parse(await readFile(join(desktopDirectory, 'package.json'), 'utf8'))
  const eslintConfig = await readFile(join(desktopDirectory, 'eslint.config.js'), 'utf8')

  assert.equal(packageJson.devDependencies['eslint-plugin-jsdoc'], '64.1.0')
  assert.equal(
    packageJson.scripts['docs:check'],
    'node scripts/check-code-documentation.mjs && eslint . --max-warnings=0',
  )
  assert.equal(packageJson.scripts.lint, 'pnpm docs:check')
  assert.match(eslintConfig, /import jsdoc from 'eslint-plugin-jsdoc'/u)
  for (const rule of ['check-syntax', 'check-tag-names', 'require-description']) {
    assert.match(eslintConfig, new RegExp(`'jsdoc/${rule}': 'error'`, 'u'))
  }
  assert.doesNotMatch(eslintConfig, /src\/\*\*.*jsdoc\//u)
})
