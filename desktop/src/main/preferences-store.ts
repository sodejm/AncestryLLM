/** Implements in-memory and file-backed desktop preference storage with bounded schemas and safe writes. */
import { randomUUID } from 'node:crypto'
import { constants, type BigIntStats } from 'node:fs'
import { lstat, mkdir, open, rename, unlink } from 'node:fs/promises'
import { isAbsolute, join } from 'node:path'
import type { LocalPreferences, PreferenceUpdate } from '../shared-contract/desktop'

const PREFERENCES_FILE_NAME = 'preferences.json'
const MAX_PREFERENCES_BYTES = 8_192
const pendingByFile = new Map<string, Promise<void>>()

export type PreferencesDiagnosticCode =
  | 'PREFERENCES_FILE_MISSING'
  | 'PREFERENCES_MIGRATED'
  | 'PREFERENCES_CORRUPT'
  | 'PREFERENCES_UNSUPPORTED_SCHEMA'
  | 'PREFERENCES_CONFLICT'
  | 'PREFERENCES_UNSAFE_STORAGE'
  | 'PREFERENCES_IO_ERROR'
  | 'PREFERENCES_INVALID_UPDATE'

export type PreferencesDiagnostic = Readonly<{ code: PreferencesDiagnosticCode }>
export type PreferencesDiagnosticSink = (diagnostic: PreferencesDiagnostic) => void

export interface PreferencesStore {
  get(): Promise<Readonly<LocalPreferences>>
  update(update: PreferenceUpdate): Promise<Readonly<LocalPreferences>>
}

export class PreferencesConflictError extends Error {
  constructor() {
    super('Preference revision conflict.')
    this.name = 'PreferencesConflictError'
  }
}

export class PreferencesStorageError extends Error {
  readonly code: PreferencesDiagnosticCode

  constructor(code: PreferencesDiagnosticCode) {
    super('Desktop preference storage is unavailable.')
    this.name = 'PreferencesStorageError'
    this.code = code
  }
}

export const DEFAULT_PREFERENCES: Readonly<LocalPreferences> = Object.freeze({
  colorScheme: 'system',
  reducedMotion: false,
  onboardingCompleted: false,
  schemaVersion: 1,
  revision: 0,
})

function frozen<T extends object>(value: T): Readonly<T> {
  return Object.freeze(value)
}

function preferenceCopy(value: Readonly<LocalPreferences>): Readonly<LocalPreferences> {
  return frozen({ ...value })
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function exactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(value)
  return actual.length === expected.length && actual.every((key) => expected.includes(key))
}

function validColorScheme(value: unknown): value is LocalPreferences['colorScheme'] {
  return value === 'system' || value === 'light' || value === 'dark'
}

function validRevision(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
}

function currentPreferences(value: unknown): Readonly<LocalPreferences> | undefined {
  if (!record(value) || !exactKeys(value, ['colorScheme', 'reducedMotion', 'onboardingCompleted', 'schemaVersion', 'revision'])) {
    return undefined
  }
  if (
    !validColorScheme(value.colorScheme)
    || typeof value.reducedMotion !== 'boolean'
    || typeof value.onboardingCompleted !== 'boolean'
    || value.schemaVersion !== 1
    || !validRevision(value.revision)
  ) {
    return undefined
  }
  return frozen({
    colorScheme: value.colorScheme,
    reducedMotion: value.reducedMotion,
    onboardingCompleted: value.onboardingCompleted,
    schemaVersion: 1,
    revision: value.revision,
  })
}

function legacyPreferences(value: unknown): Readonly<LocalPreferences> | undefined {
  if (!record(value) || !exactKeys(value, ['colorScheme', 'reducedMotion', 'schemaVersion', 'revision'])) return undefined
  if (
    !validColorScheme(value.colorScheme)
    || typeof value.reducedMotion !== 'boolean'
    || value.schemaVersion !== 0
    || !validRevision(value.revision)
  ) {
    return undefined
  }
  return frozen({
    colorScheme: value.colorScheme,
    reducedMotion: value.reducedMotion,
    onboardingCompleted: false,
    schemaVersion: 1,
    revision: value.revision,
  })
}

function unsupportedSchema(value: unknown): boolean {
  return record(value)
    && typeof value.schemaVersion === 'number'
    && Number.isSafeInteger(value.schemaVersion)
    && value.schemaVersion !== 0
    && value.schemaVersion !== 1
}

function validUpdate(value: unknown): value is PreferenceUpdate {
  if (!record(value)) return false
  const allowed = ['expectedRevision', 'colorScheme', 'reducedMotion', 'onboardingCompleted']
  if (!Object.keys(value).every((key) => allowed.includes(key))) return false
  if (Object.keys(value).length < 2 || !validRevision(value.expectedRevision)) return false
  if ('colorScheme' in value && !validColorScheme(value.colorScheme)) return false
  if ('reducedMotion' in value && typeof value.reducedMotion !== 'boolean') return false
  return !('onboardingCompleted' in value) || typeof value.onboardingCompleted === 'boolean'
}

export class MemoryPreferencesStore implements PreferencesStore {
  private current: Readonly<LocalPreferences> = DEFAULT_PREFERENCES

  get(): Promise<Readonly<LocalPreferences>> {
    return Promise.resolve(this.current)
  }

  update(update: PreferenceUpdate): Promise<Readonly<LocalPreferences>> {
    if (update.expectedRevision !== this.current.revision) return Promise.reject(new PreferencesConflictError())
    this.current = frozen({
      colorScheme: update.colorScheme ?? this.current.colorScheme,
      reducedMotion: update.reducedMotion ?? this.current.reducedMotion,
      onboardingCompleted: update.onboardingCompleted ?? this.current.onboardingCompleted,
      schemaVersion: 1,
      revision: this.current.revision + 1,
    })
    return Promise.resolve(this.current)
  }
}

type ReadState =
  | Readonly<{ kind: 'missing'; preferences: Readonly<LocalPreferences> }>
  | Readonly<{ kind: 'current'; preferences: Readonly<LocalPreferences> }>
  | Readonly<{ kind: 'migrated'; preferences: Readonly<LocalPreferences> }>
  | Readonly<{ kind: 'corrupt'; preferences: Readonly<LocalPreferences> }>
  | Readonly<{ kind: 'unsupported'; preferences: Readonly<LocalPreferences> }>

function missing(error: unknown): boolean {
  return record(error) && error.code === 'ENOENT'
}

function sameFileIdentity(left: BigIntStats, right: BigIntStats): boolean {
  return left.ino !== 0n && right.ino !== 0n && left.dev === right.dev && left.ino === right.ino
}

function serializeFileOperation<T>(file: string, operation: () => Promise<T>): Promise<T> {
  const previous = pendingByFile.get(file) ?? Promise.resolve()
  const result = previous.then(operation, operation)
  const settled = result.then(() => undefined, () => undefined)
  pendingByFile.set(file, settled)
  void settled.then(() => {
    if (pendingByFile.get(file) === settled) pendingByFile.delete(file)
  })
  return result
}

export class FilePreferencesStore implements PreferencesStore {
  private readonly directory: string
  private readonly file: string
  private readonly onDiagnostic: PreferencesDiagnosticSink

  constructor(directory: string, onDiagnostic: PreferencesDiagnosticSink = () => undefined) {
    if (!isAbsolute(directory)) throw new PreferencesStorageError('PREFERENCES_UNSAFE_STORAGE')
    this.directory = directory
    this.file = join(directory, PREFERENCES_FILE_NAME)
    this.onDiagnostic = onDiagnostic
  }

  get(): Promise<Readonly<LocalPreferences>> {
    return this.serialized(async () => preferenceCopy((await this.readState()).preferences))
  }

  update(update: PreferenceUpdate): Promise<Readonly<LocalPreferences>> {
    return this.serialized(async () => {
      if (!validUpdate(update)) return this.fail('PREFERENCES_INVALID_UPDATE')
      const state = await this.readState()
      if (state.kind === 'corrupt' || state.kind === 'unsupported') return this.fail(
        state.kind === 'corrupt' ? 'PREFERENCES_CORRUPT' : 'PREFERENCES_UNSUPPORTED_SCHEMA',
      )
      if (update.expectedRevision !== state.preferences.revision) {
        this.diagnostic('PREFERENCES_CONFLICT')
        throw new PreferencesConflictError()
      }
      if (state.preferences.revision === Number.MAX_SAFE_INTEGER) return this.fail('PREFERENCES_INVALID_UPDATE')
      const next = frozen({
        colorScheme: update.colorScheme ?? state.preferences.colorScheme,
        reducedMotion: update.reducedMotion ?? state.preferences.reducedMotion,
        onboardingCompleted: update.onboardingCompleted ?? state.preferences.onboardingCompleted,
        schemaVersion: 1 as const,
        revision: state.preferences.revision + 1,
      })
      await this.write(next)
      return preferenceCopy(next)
    })
  }

  private serialized<T>(operation: () => Promise<T>): Promise<T> {
    return serializeFileOperation(this.file, operation)
  }

  private diagnostic(code: PreferencesDiagnosticCode): void {
    try {
      this.onDiagnostic(frozen({ code }))
    } catch {
      // Diagnostics must never change storage behavior.
    }
  }

  private fail(code: PreferencesDiagnosticCode): never {
    this.diagnostic(code)
    throw new PreferencesStorageError(code)
  }

  private async directoryExists(): Promise<boolean> {
    try {
      const metadata = await lstat(this.directory)
      if (metadata.isSymbolicLink() || !metadata.isDirectory()) return this.fail('PREFERENCES_UNSAFE_STORAGE')
      return true
    } catch (cause) {
      if (missing(cause)) return false
      return this.fail('PREFERENCES_IO_ERROR')
    }
  }

  private async readState(): Promise<ReadState> {
    if (!await this.directoryExists()) {
      this.diagnostic('PREFERENCES_FILE_MISSING')
      return frozen({ kind: 'missing', preferences: DEFAULT_PREFERENCES })
    }
    let handle
    try {
      const metadata = await lstat(this.file, { bigint: true })
      if (metadata.isSymbolicLink() || !metadata.isFile()) return this.fail('PREFERENCES_UNSAFE_STORAGE')
      handle = await open(this.file, constants.O_RDONLY | constants.O_NOFOLLOW)
      const openedMetadata = await handle.stat({ bigint: true })
      let currentMetadata: BigIntStats
      try {
        currentMetadata = await lstat(this.file, { bigint: true })
      } catch {
        return this.fail('PREFERENCES_UNSAFE_STORAGE')
      }
      if (
        !openedMetadata.isFile()
        || currentMetadata.isSymbolicLink()
        || !currentMetadata.isFile()
        || !sameFileIdentity(metadata, openedMetadata)
        || !sameFileIdentity(currentMetadata, openedMetadata)
      ) return this.fail('PREFERENCES_UNSAFE_STORAGE')
      if (openedMetadata.size > BigInt(MAX_PREFERENCES_BYTES)) {
        this.diagnostic('PREFERENCES_CORRUPT')
        return frozen({ kind: 'corrupt', preferences: DEFAULT_PREFERENCES })
      }
      const buffer = Buffer.alloc(MAX_PREFERENCES_BYTES + 1)
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, 0)
      if (bytesRead > MAX_PREFERENCES_BYTES) {
        this.diagnostic('PREFERENCES_CORRUPT')
        return frozen({ kind: 'corrupt', preferences: DEFAULT_PREFERENCES })
      }
      let parsed: unknown
      try {
        parsed = JSON.parse(buffer.subarray(0, bytesRead).toString('utf8')) as unknown
      } catch {
        this.diagnostic('PREFERENCES_CORRUPT')
        return frozen({ kind: 'corrupt', preferences: DEFAULT_PREFERENCES })
      }
      const current = currentPreferences(parsed)
      if (current) return frozen({ kind: 'current', preferences: current })
      const migrated = legacyPreferences(parsed)
      if (migrated) {
        this.diagnostic('PREFERENCES_MIGRATED')
        return frozen({ kind: 'migrated', preferences: migrated })
      }
      const kind = unsupportedSchema(parsed) ? 'unsupported' : 'corrupt'
      this.diagnostic(kind === 'unsupported' ? 'PREFERENCES_UNSUPPORTED_SCHEMA' : 'PREFERENCES_CORRUPT')
      return frozen({ kind, preferences: DEFAULT_PREFERENCES })
    } catch (cause) {
      if (missing(cause)) {
        this.diagnostic('PREFERENCES_FILE_MISSING')
        return frozen({ kind: 'missing', preferences: DEFAULT_PREFERENCES })
      }
      if (cause instanceof PreferencesStorageError) throw cause
      return this.fail('PREFERENCES_IO_ERROR')
    } finally {
      await handle?.close().catch(() => undefined)
    }
  }

  private async write(preferences: Readonly<LocalPreferences>): Promise<void> {
    if (!await this.directoryExists()) {
      try {
        await mkdir(this.directory, { mode: 0o700, recursive: true })
      } catch {
        return this.fail('PREFERENCES_IO_ERROR')
      }
      if (!await this.directoryExists()) return this.fail('PREFERENCES_IO_ERROR')
    }
    try {
      const target = await lstat(this.file)
      if (target.isSymbolicLink() || !target.isFile()) return this.fail('PREFERENCES_UNSAFE_STORAGE')
    } catch (cause) {
      if (!missing(cause)) {
        if (cause instanceof PreferencesStorageError) throw cause
        return this.fail('PREFERENCES_IO_ERROR')
      }
    }

    const temporary = join(this.directory, `.${PREFERENCES_FILE_NAME}.${process.pid}.${randomUUID()}.tmp`)
    let handle
    try {
      handle = await open(temporary, constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY | constants.O_NOFOLLOW, 0o600)
      await handle.writeFile(`${JSON.stringify(preferences)}\n`, 'utf8')
      await handle.sync()
      await handle.close()
      handle = undefined
      try {
        const target = await lstat(this.file)
        if (target.isSymbolicLink() || !target.isFile()) return this.fail('PREFERENCES_UNSAFE_STORAGE')
      } catch (cause) {
        if (!missing(cause)) {
          if (cause instanceof PreferencesStorageError) throw cause
          return this.fail('PREFERENCES_IO_ERROR')
        }
      }
      await rename(temporary, this.file)
    } catch (cause) {
      if (cause instanceof PreferencesStorageError) throw cause
      return this.fail('PREFERENCES_IO_ERROR')
    } finally {
      await handle?.close().catch(() => undefined)
      await unlink(temporary).catch(() => undefined)
    }
  }
}
