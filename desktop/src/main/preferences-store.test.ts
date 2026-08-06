/** Tests bounded desktop preference persistence, optimistic concurrency, and unsafe-storage detection. */
import { mkdtemp, readFile, readdir, rm, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  FilePreferencesStore,
  PreferencesConflictError,
  PreferencesStorageError,
  type PreferencesDiagnostic,
} from './preferences-store'

const directories: string[] = []

async function temporaryDirectory(): Promise<string> {
  const directory = await mkdtemp(join(tmpdir(), 'ancestryllm-preferences-'))
  directories.push(directory)
  return directory
}

afterEach(async () => {
  await Promise.all(directories.splice(0).map((directory) => rm(directory, { force: true, recursive: true })))
})

describe('FilePreferencesStore', () => {
  it('returns the exact safe defaults when the preference file is missing', async () => {
    const directory = await temporaryDirectory()
    const diagnostics: PreferencesDiagnostic[] = []
    const store = new FilePreferencesStore(directory, (diagnostic) => diagnostics.push(diagnostic))

    await expect(store.get()).resolves.toEqual({
      colorScheme: 'system',
      reducedMotion: false,
      onboardingCompleted: false,
      schemaVersion: 1,
      revision: 0,
    })
    expect(diagnostics).toEqual([{ code: 'PREFERENCES_FILE_MISSING' }])
  })

  it('persists completed onboarding in the bounded schema and survives a store restart', async () => {
    const directory = await temporaryDirectory()
    const store = new FilePreferencesStore(directory)

    await expect(store.update({
      expectedRevision: 0,
      colorScheme: 'dark',
      reducedMotion: true,
      onboardingCompleted: true,
    })).resolves.toEqual({
      colorScheme: 'dark',
      reducedMotion: true,
      onboardingCompleted: true,
      schemaVersion: 1,
      revision: 1,
    })
    await expect(new FilePreferencesStore(directory).get()).resolves.toEqual({
      colorScheme: 'dark',
      reducedMotion: true,
      onboardingCompleted: true,
      schemaVersion: 1,
      revision: 1,
    })

    const persisted = JSON.parse(await readFile(join(directory, 'preferences.json'), 'utf8')) as Record<string, unknown>
    expect(Object.keys(persisted).sort()).toEqual([
      'colorScheme',
      'onboardingCompleted',
      'reducedMotion',
      'revision',
      'schemaVersion',
    ])
    expect(await readdir(directory)).toEqual(['preferences.json'])
  })

  it('serializes writes and rejects an optimistic revision conflict', async () => {
    const directory = await temporaryDirectory()
    const store = new FilePreferencesStore(directory)

    const writes = await Promise.allSettled([
      store.update({ expectedRevision: 0, colorScheme: 'dark' }),
      store.update({ expectedRevision: 0, reducedMotion: true }),
    ])

    expect(writes.filter((result) => result.status === 'fulfilled')).toHaveLength(1)
    const rejected = writes.find((result) => result.status === 'rejected')
    expect(rejected).toMatchObject({ status: 'rejected', reason: expect.any(PreferencesConflictError) })
    await expect(store.get()).resolves.toMatchObject({ revision: 1 })
  })

  it('serializes same-path writes across store instances', async () => {
    const directory = await temporaryDirectory()
    const firstStore = new FilePreferencesStore(directory)
    const secondStore = new FilePreferencesStore(directory)

    const writes = await Promise.allSettled([
      firstStore.update({ expectedRevision: 0, colorScheme: 'dark' }),
      secondStore.update({ expectedRevision: 0, reducedMotion: true }),
    ])

    expect(writes.filter((result) => result.status === 'fulfilled')).toHaveLength(1)
    const rejected = writes.find((result) => result.status === 'rejected')
    expect(rejected).toMatchObject({ status: 'rejected', reason: expect.any(PreferencesConflictError) })
    await expect(new FilePreferencesStore(directory).get()).resolves.toMatchObject({ revision: 1 })
  })

  it('returns defaults for corrupt data and refuses to overwrite it with path-free diagnostics', async () => {
    const directory = await temporaryDirectory()
    const file = join(directory, 'preferences.json')
    await writeFile(file, '{not-json', 'utf8')
    const diagnostics: PreferencesDiagnostic[] = []
    const store = new FilePreferencesStore(directory, (diagnostic) => diagnostics.push(diagnostic))

    await expect(store.get()).resolves.toMatchObject({ onboardingCompleted: false, revision: 0 })
    const failure = await store.update({ expectedRevision: 0, colorScheme: 'dark' }).catch((cause: unknown) => cause)
    expect(failure).toBeInstanceOf(PreferencesStorageError)
    expect(String(failure)).not.toContain(directory)
    expect(await readFile(file, 'utf8')).toBe('{not-json')
    expect(JSON.stringify(diagnostics)).not.toContain(directory)
    expect(diagnostics).toContainEqual({ code: 'PREFERENCES_CORRUPT' })
  })

  it('adds onboarding defaults when reading the supported legacy schema', async () => {
    const directory = await temporaryDirectory()
    await writeFile(
      join(directory, 'preferences.json'),
      JSON.stringify({ colorScheme: 'light', reducedMotion: true, schemaVersion: 0, revision: 7 }),
      'utf8',
    )
    const store = new FilePreferencesStore(directory)

    await expect(store.get()).resolves.toEqual({
      colorScheme: 'light',
      reducedMotion: true,
      onboardingCompleted: false,
      schemaVersion: 1,
      revision: 7,
    })
    await expect(store.update({ expectedRevision: 7, onboardingCompleted: true })).resolves.toMatchObject({
      onboardingCompleted: true,
      schemaVersion: 1,
      revision: 8,
    })
  })

  it('fails closed without replacing an unsupported schema version', async () => {
    const directory = await temporaryDirectory()
    const file = join(directory, 'preferences.json')
    const future = JSON.stringify({ colorScheme: 'dark', reducedMotion: true, onboardingCompleted: true, schemaVersion: 2, revision: 9 })
    await writeFile(file, future, 'utf8')
    const store = new FilePreferencesStore(directory)

    await expect(store.get()).resolves.toMatchObject({ colorScheme: 'system', onboardingCompleted: false, revision: 0 })
    await expect(store.update({ expectedRevision: 0, colorScheme: 'light' })).rejects.toBeInstanceOf(PreferencesStorageError)
    expect(await readFile(file, 'utf8')).toBe(future)
  })

  it('rejects unbounded update fields before creating storage', async () => {
    const directory = await temporaryDirectory()
    const store = new FilePreferencesStore(directory)

    await expect(store.update({ expectedRevision: 0, colorScheme: 'dark', provider: 'openai' } as never)).rejects.toMatchObject({
      code: 'PREFERENCES_INVALID_UPDATE',
    })
    expect(await readdir(directory)).toEqual([])
  })

  it.runIf(process.platform !== 'win32')('rejects a symlink preference file without touching its target', async () => {
    const directory = await temporaryDirectory()
    const target = join(directory, 'outside.json')
    await writeFile(target, '{"secret":"unchanged"}', 'utf8')
    await symlink(target, join(directory, 'preferences.json'))

    const store = new FilePreferencesStore(directory)
    await expect(store.get()).rejects.toBeInstanceOf(PreferencesStorageError)
    await expect(store.update({ expectedRevision: 0, colorScheme: 'dark' })).rejects.toBeInstanceOf(PreferencesStorageError)
    expect(await readFile(target, 'utf8')).toBe('{"secret":"unchanged"}')
  })

  it('rejects a file whose opened identity differs from its path identity', async () => {
    const payload = Buffer.from(JSON.stringify({
      colorScheme: 'dark',
      reducedMotion: true,
      onboardingCompleted: true,
      schemaVersion: 1,
      revision: 7,
    }))
    const directoryMetadata = {
      isDirectory: () => true,
      isSymbolicLink: () => false,
    }
    const pathMetadata = {
      dev: 1n,
      ino: 1n,
      isFile: () => true,
      isSymbolicLink: () => false,
    }
    const openedMetadata = {
      dev: 2n,
      ino: 2n,
      size: payload.length,
      isFile: () => true,
    }

    vi.resetModules()
    vi.doMock('node:fs/promises', async (importOriginal) => {
      const original = await importOriginal<typeof import('node:fs/promises')>()
      const lstatMock = vi.fn(async (path: string) => path.endsWith('preferences.json') ? pathMetadata : directoryMetadata)
      const mkdirMock = vi.fn()
      const openMock = vi.fn(async () => ({
        close: vi.fn(async () => undefined),
        read: vi.fn(async (buffer: Buffer) => {
          payload.copy(buffer)
          return { buffer, bytesRead: payload.length }
        }),
        stat: vi.fn(async () => openedMetadata),
      }))
      const renameMock = vi.fn()
      const unlinkMock = vi.fn()
      return {
        ...original,
        default: {
          ...original,
          lstat: lstatMock,
          mkdir: mkdirMock,
          open: openMock,
          rename: renameMock,
          unlink: unlinkMock,
        },
        lstat: lstatMock,
        mkdir: mkdirMock,
        open: openMock,
        rename: renameMock,
        unlink: unlinkMock,
      }
    })
    try {
      const { FilePreferencesStore: IsolatedPreferencesStore } = await import('./preferences-store')
      await expect(new IsolatedPreferencesStore('/virtual/ancestryllm').get()).rejects.toMatchObject({
        code: 'PREFERENCES_UNSAFE_STORAGE',
      })
    } finally {
      vi.doUnmock('node:fs/promises')
      vi.resetModules()
    }
  })
})
