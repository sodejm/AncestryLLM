/** Enforces meaningful documentation on exported desktop source declarations. */
import { readdir, readFile } from 'node:fs/promises'
import { relative, resolve, sep } from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const SOURCE_EXTENSIONS = new Set(['.js', '.mjs', '.ts', '.tsx'])
const IGNORED_DIRECTORIES = new Set(['.git', 'node_modules', 'out', 'release'])
const PLACEHOLDER_WORDS = new Set(['doc', 'docs', 'documentation', 'fixme', 'placeholder', 'tbd', 'todo'])
const PLACEHOLDER_MARKERS = new Set(['fixme', 'placeholder', 'tbd', 'todo'])
const SECURITY_BOUNDARY_DECLARATIONS = new Map([
  ['src/main/container-process.ts', [
    'validEnvironment',
    'validateRequest',
    'parseRealizedContainer',
    'parseRealizedNetwork',
  ]],
  ['src/main/container-supervisor.ts', [
    'validateClone',
    'safeAbsolutePath',
    'parseCompose',
    'validateRuntimeObservation',
    'validateInventory',
    'validateRealizedResources',
    'assertExpectedPostOperationResources',
  ]],
  ['src/main/file-grant-broker.ts', [
    'selectedPath',
    'validateRegularFile',
    'inspectInput',
    'inspectOutput',
  ]],
  ['src/main/ipc-handlers.ts', [
    'eventParts',
    'safeResponse',
    'runOperation',
    'schedule',
    'invalidate',
    'registerNoArgumentHandler',
  ]],
  ['src/main/macos-arm64-runtime-manager.ts', [
    'digestMatches',
    'safeRoot',
    'safeDirectory',
    'atomicJson',
    'parseOwnership',
    'allowedRedirect',
    'requestDownload',
    'downloadResponse',
  ]],
  ['src/main/macos-arm64-runtime-policy.ts', [
    'safeRelativePath',
    'checkedDigest',
    'parseTar',
    'verifiedOutputRoot',
    'ensureParents',
  ]],
  ['src/main/native-file-dialogs.ts', ['invokeDialog', 'ownerWindow']],
  ['src/main/preferences-store.ts', [
    'currentPreferences',
    'legacyPreferences',
    'validUpdate',
    'serializeFileOperation',
  ]],
  ['src/main/security-policy.ts', ['responseHeaders', 'requiredPreferences']],
  ['src/main/sidecar-client.ts', [
    'requestFixedRoute',
    'validJsonResponse',
    'parseJson',
    'streamFixedJobEvents',
    'streamFixedChatEvents',
  ]],
  ['src/main/sidecar-integrity.ts', [
    'safeRelativePath',
    'parseManifest',
    'withinTarget',
    'inventory',
    'verifyEntry',
    'verifyPayload',
  ]],
  ['src/main/sidecar-process.ts', [
    'executeWindowsTreeKill',
    'parseReadyFrame',
    'readiness',
    'expectedProof',
  ]],
  ['src/main/sidecar-supervisor.ts', [
    'validateLinuxKeyringVerificationRoot',
    'requireCompatible',
  ]],
  ['src/main/structured-clone-policy.ts', ['byteLength']],
])

function parseArguments(argumentsList) {
  let root = resolve(fileURLToPath(new URL('..', import.meta.url)))
  for (let index = 0; index < argumentsList.length; index += 1) {
    if (argumentsList[index] !== '--root' || index + 1 >= argumentsList.length) {
      throw new Error('usage: check-code-documentation.mjs [--root <directory>]')
    }
    root = resolve(argumentsList[index + 1])
    index += 1
  }
  return { root }
}

function extensionFor(path) {
  if (path.endsWith('.d.ts')) {
    return '.ts'
  }
  return [...SOURCE_EXTENSIONS].find((extension) => path.endsWith(extension))
}

function isTestSource(path) {
  return /(?:^|\/)[^/]+\.(?:test|spec)\.(?:js|mjs|ts|tsx)$/u.test(path)
}

async function sourceFiles(root) {
  const files = []
  async function visit(directory) {
    const entries = await readdir(directory, { withFileTypes: true })
    for (const entry of entries.sort((left, right) => left.name.localeCompare(right.name))) {
      const absolutePath = resolve(directory, entry.name)
      if (entry.isDirectory()) {
        if (!IGNORED_DIRECTORIES.has(entry.name)) {
          await visit(absolutePath)
        }
      } else if (entry.isFile() && extensionFor(entry.name) !== undefined) {
        files.push(absolutePath)
      }
    }
  }
  await visit(root)
  return files
}

function meaningfulDocumentation(comment) {
  const cleaned = comment
    .replace(/^\/\*\*/u, '')
    .replace(/\*\/$/u, '')
    .split(/\r?\n/u)
    .map((line) => line.replace(/^\s*\*?\s?/u, '').replace(/^@[A-Za-z-]+\s*/u, '').trim())
    .filter(Boolean)
    .join(' ')
    .trim()
  const words = cleaned
    .replaceAll('_', ' ')
    .split(/\s+/u)
    .map((word) => word.toLowerCase().replaceAll(/[^a-z0-9]/gu, ''))
    .filter((word) => /[a-z]/u.test(word))
  return (
    cleaned.length >= 12
    && words.length >= 2
    && words.some((word) => !PLACEHOLDER_WORDS.has(word))
    && words.every((word) => !PLACEHOLDER_MARKERS.has(word))
  )
}

function moduleHeaderRange(source) {
  const match = source.match(
    /^(?:\uFEFF)?(?:#![^\r\n]*(?:\r?\n|$))?(?:\s|\/\/[^\r\n]*(?:\r?\n|$)|\/\*(?!\*)[\s\S]*?\*\/)*(\/\*\*[\s\S]*?\*\/)/u,
  )
  if (match === null) {
    return undefined
  }
  const start = source.indexOf(match[1])
  return { start, end: start + match[1].length }
}

function leadingDocumentation(node, sourceFile, source, headerRange) {
  const triviaStart = node.getFullStart()
  const declarationStart = node.getStart(sourceFile)
  const trivia = source.slice(triviaStart, declarationStart)
  const comments = [...trivia.matchAll(/\/\*\*[\s\S]*?\*\//gu)]
  const comment = comments.at(-1)
  if (comment === undefined || comment.index === undefined) {
    return undefined
  }
  const start = triviaStart + comment.index
  const end = start + comment[0].length
  if (headerRange !== undefined && start === headerRange.start && end === headerRange.end) {
    return undefined
  }
  return comment[0]
}

function hasExportModifier(node) {
  return (ts.getModifiers(node) ?? []).some((modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword)
}

function bindingNames(name) {
  if (ts.isIdentifier(name)) {
    return [name.text]
  }
  return name.elements.flatMap((element) => ts.isOmittedExpression(element) ? [] : bindingNames(element.name))
}

function declarationNames(statement) {
  if (ts.isVariableStatement(statement)) {
    return statement.declarationList.declarations.flatMap((declaration) => bindingNames(declaration.name))
  }
  if ('name' in statement && statement.name !== undefined && ts.isIdentifier(statement.name)) {
    return [statement.name.text]
  }
  return []
}

function localDeclarations(sourceFile) {
  const localDeclarations = new Map()
  for (const statement of sourceFile.statements) {
    for (const name of declarationNames(statement)) {
      localDeclarations.set(name, statement)
    }
  }
  return localDeclarations
}

function sourceCandidates(sourceFile, declarations) {
  const candidates = []

  for (const statement of sourceFile.statements) {
    if (hasExportModifier(statement)) {
      const names = declarationNames(statement)
      candidates.push({ node: statement, names: names.length === 0 ? ['default'] : names })
    }
    if (ts.isExportAssignment(statement)) {
      candidates.push({ node: statement, names: ['default'] })
    }
  }

  for (const statement of sourceFile.statements) {
    if (!ts.isExportDeclaration(statement) || statement.exportClause === undefined) {
      if (ts.isExportDeclaration(statement) && statement.moduleSpecifier !== undefined) {
        candidates.push({ node: statement, names: ['*'] })
      }
      continue
    }
    if (!ts.isNamedExports(statement.exportClause)) {
      candidates.push({ node: statement, names: [statement.exportClause.name.text] })
      continue
    }
    if (statement.exportClause.elements.length === 0) {
      continue
    }
    for (const element of statement.exportClause.elements) {
      const localName = (element.propertyName ?? element.name).text
      const declaration = statement.moduleSpecifier === undefined ? declarations.get(localName) : undefined
      candidates.push({ node: declaration ?? statement, names: [element.name.text] })
    }
  }

  return candidates
}

function scriptKind(path) {
  if (path.endsWith('.tsx')) {
    return ts.ScriptKind.TSX
  }
  if (path.endsWith('.ts')) {
    return ts.ScriptKind.TS
  }
  if (path.endsWith('.mjs') || path.endsWith('.js')) {
    return ts.ScriptKind.JS
  }
  return ts.ScriptKind.Unknown
}

async function checkFile(absolutePath, root) {
  const source = await readFile(absolutePath, 'utf8')
  const path = relative(root, absolutePath).split(sep).join('/')
  if (isTestSource(path)) {
    return []
  }
  const sourceFile = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, scriptKind(path))
  const headerRange = moduleHeaderRange(source)
  const declarations = localDeclarations(sourceFile)
  const diagnostics = []
  const seen = new Set()
  for (const candidate of sourceCandidates(sourceFile, declarations)) {
    const documentation = leadingDocumentation(candidate.node, sourceFile, source, headerRange)
    const rule = documentation === undefined
      ? 'missing-export-documentation'
      : meaningfulDocumentation(documentation)
        ? undefined
        : 'placeholder-export-documentation'
    if (rule === undefined) {
      continue
    }
    for (const name of candidate.names) {
      const diagnostic = `${path}:${rule}:${name}`
      if (!seen.has(diagnostic)) {
        diagnostics.push(diagnostic)
        seen.add(diagnostic)
      }
    }
  }
  for (const name of SECURITY_BOUNDARY_DECLARATIONS.get(path) ?? []) {
    const declaration = declarations.get(name)
    if (declaration === undefined) {
      diagnostics.push(`${path}:security-boundary-declaration-missing:${name}`)
      continue
    }
    const documentation = leadingDocumentation(declaration, sourceFile, source, headerRange)
    const rule = documentation === undefined
      ? 'missing-security-boundary-documentation'
      : meaningfulDocumentation(documentation)
        ? undefined
        : 'placeholder-security-boundary-documentation'
    if (rule !== undefined) {
      diagnostics.push(`${path}:${rule}:${name}`)
    }
  }
  return diagnostics
}

async function main() {
  let root
  try {
    ({ root } = parseArguments(process.argv.slice(2)))
  } catch (error) {
    console.error(`desktop_code_documentation: invalid-arguments: ${error.message}`)
    return 2
  }

  const diagnostics = []
  try {
    for (const path of await sourceFiles(root)) {
      diagnostics.push(...await checkFile(path, root))
    }
  } catch {
    console.error('desktop_code_documentation: filesystem-error')
    return 2
  }
  if (diagnostics.length > 0) {
    for (const diagnostic of diagnostics.sort()) {
      console.log(diagnostic)
    }
    console.error(`desktop_code_documentation: ${diagnostics.length} violation(s) found.`)
    return 1
  }
  console.log('desktop_code_documentation: all checks passed.')
  return 0
}

process.exitCode = await main()
