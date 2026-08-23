/** Verifies opaque file grants remain bounded, owner-scoped, and stale-safe. */
import { link, lstat, mkdir, mkdtemp, readFile, readdir, rename, symlink, truncate, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { basename, join, sep } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  FileGrantBroker,
  FileGrantBrokerError,
  type FilePublicationPort,
  type NativeFileDialogPort,
} from './file-grant-broker'

const temporaryRoots: string[] = []

async function temporaryRoot(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), 'ancestryllm-file-grants-'))
  temporaryRoots.push(root)
  return root
}

function dialogs(overrides: Partial<NativeFileDialogPort> = {}): NativeFileDialogPort {
  return {
    selectOpenFile: vi.fn().mockResolvedValue(null),
    selectSaveFile: vi.fn().mockResolvedValue(null),
    confirmReplacement: vi.fn().mockResolvedValue(false),
    ...overrides,
  }
}

afterEach(async () => {
  const { rm } = await import('node:fs/promises')
  await Promise.all(temporaryRoots.splice(0).map((root) => rm(root, { recursive: true, force: true })))
})

describe('opaque file-grant broker', () => {
  it('issues a path-free, high-entropy, owner- and purpose-scoped GEDCOM grant', async () => {
    const root = await temporaryRoot()
    const path = join(root, 'fictional.ged')
    await writeFile(path, '0 HEAD\n1 SOUR TEST\n0 TRLR\n')
    const broker = new FileGrantBroker(dialogs({
      selectOpenFile: vi.fn().mockResolvedValue(path),
    }))
    const owner = {}

    const grant = await broker.requestOpenGrant(owner, { purpose: 'gedcom-read' })

    expect(grant).toMatchObject({
      purpose: 'gedcom-read',
      access: 'read',
      metadata: {
        displayName: 'fictional.ged',
        format: 'gedcom',
        sizeBytes: 26,
        validation: 'validated-input',
      },
    })
    expect(grant?.grantId).toMatch(/^grt_[a-f0-9]{64}$/)
    expect(JSON.stringify(grant)).not.toContain(root)
    await expect(broker.resolveReadGrant({}, grant!.grantId, 'gedcom-read')).rejects.toMatchObject({ code: 'FILE_GRANT_FORBIDDEN' })
    await expect(broker.resolveReadGrant(owner, grant!.grantId, 'rootsmagic-read')).rejects.toMatchObject({ code: 'FILE_GRANT_FORBIDDEN' })
    await expect(broker.resolveReadGrant(owner, grant!.grantId, 'gedcom-read')).resolves.toMatchObject({ path })
    await expect(broker.resolveReadGrant(owner, grant!.grantId, 'gedcom-read')).rejects.toMatchObject({ code: 'FILE_GRANT_REVOKED' })
  })

  it('rejects unsupported content, directories, and symlink selections', async () => {
    const root = await temporaryRoot()
    const invalid = join(root, 'not-gedcom.ged')
    await writeFile(invalid, 'not a GEDCOM')
    const broker = new FileGrantBroker(dialogs({ selectOpenFile: vi.fn().mockResolvedValue(invalid) }))
    await expect(broker.requestOpenGrant({}, { purpose: 'gedcom-read' })).rejects.toMatchObject({ code: 'FILE_SELECTION_INVALID' })

    const directoryBroker = new FileGrantBroker(dialogs({ selectOpenFile: vi.fn().mockResolvedValue(root) }))
    await expect(directoryBroker.requestOpenGrant({}, { purpose: 'gedcom-read' })).rejects.toMatchObject({ code: 'FILE_SELECTION_INVALID' })

    const valid = join(root, 'valid.ged')
    const alias = join(root, 'alias.ged')
    await writeFile(valid, '0 HEAD\n0 TRLR\n')
    try {
      await symlink(valid, alias)
      const aliasBroker = new FileGrantBroker(dialogs({ selectOpenFile: vi.fn().mockResolvedValue(alias) }))
      await expect(aliasBroker.requestOpenGrant({}, { purpose: 'gedcom-read' })).rejects.toMatchObject({ code: 'FILE_SELECTION_INVALID' })
    } catch (error) {
      if (process.platform !== 'win32') throw error
    }
  })

  it('rejects alternate path spellings, hard links, FIFOs, and oversized sparse inputs', async () => {
    const root = await temporaryRoot()
    const valid = join(root, 'valid.ged')
    await writeFile(valid, '0 HEAD\n0 TRLR\n')

    const nonCanonical = `${root}${sep}nested${sep}..${sep}valid.ged`
    const nonCanonicalBroker = new FileGrantBroker(dialogs({
      selectOpenFile: vi.fn().mockResolvedValue(nonCanonical),
    }))
    await expect(nonCanonicalBroker.requestOpenGrant({}, { purpose: 'gedcom-read' }))
      .rejects.toMatchObject({ code: 'FILE_SELECTION_INVALID' })

    const hardLink = join(root, 'hard-link.ged')
    await link(valid, hardLink)
    const hardLinkBroker = new FileGrantBroker(dialogs({
      selectOpenFile: vi.fn().mockResolvedValue(hardLink),
    }))
    await expect(hardLinkBroker.requestOpenGrant({}, { purpose: 'gedcom-read' }))
      .rejects.toMatchObject({ code: 'FILE_SELECTION_INVALID' })

    if (process.platform !== 'win32') {
      const fifo = join(root, 'pipe.ged')
      const { execFile } = await import('node:child_process')
      const { promisify } = await import('node:util')
      await promisify(execFile)('mkfifo', [fifo])
      const fifoBroker = new FileGrantBroker(dialogs({
        selectOpenFile: vi.fn().mockResolvedValue(fifo),
      }))
      await expect(fifoBroker.requestOpenGrant({}, { purpose: 'gedcom-read' }))
        .rejects.toMatchObject({ code: 'FILE_SELECTION_INVALID' })
    }

    const oversized = join(root, 'oversized.ged')
    await writeFile(oversized, '0 HEAD\n')
    await truncate(oversized, 536_870_913)
    const oversizedBroker = new FileGrantBroker(dialogs({
      selectOpenFile: vi.fn().mockResolvedValue(oversized),
    }))
    await expect(oversizedBroker.requestOpenGrant({}, { purpose: 'gedcom-read' }))
      .rejects.toMatchObject({ code: 'FILE_TOO_LARGE' })
  })

  it('validates RootsMagic content instead of trusting its extension', async () => {
    const root = await temporaryRoot()
    const valid = join(root, 'fictional.rmtree')
    await writeFile(valid, Buffer.concat([Buffer.from('SQLite format 3\0', 'ascii'), Buffer.alloc(128)]))
    const validBroker = new FileGrantBroker(dialogs({
      selectOpenFile: vi.fn().mockResolvedValue(valid),
    }))
    await expect(validBroker.requestOpenGrant({}, { purpose: 'rootsmagic-read' })).resolves.toMatchObject({
      metadata: { format: 'rootsmagic', validation: 'validated-input' },
    })

    const disguised = join(root, 'disguised.rmtree')
    await writeFile(disguised, 'not sqlite')
    const invalidBroker = new FileGrantBroker(dialogs({
      selectOpenFile: vi.fn().mockResolvedValue(disguised),
    }))
    await expect(invalidBroker.requestOpenGrant({}, { purpose: 'rootsmagic-read' }))
      .rejects.toMatchObject({ code: 'FILE_SELECTION_INVALID' })
  })

  it('fails closed when a granted input changes before its one-time resolution', async () => {
    const root = await temporaryRoot()
    const path = join(root, 'changing.ged')
    await writeFile(path, '0 HEAD\n0 TRLR\n')
    const broker = new FileGrantBroker(dialogs({ selectOpenFile: vi.fn().mockResolvedValue(path) }))
    const owner = {}
    const grant = await broker.requestOpenGrant(owner, { purpose: 'gedcom-read' })
    await writeFile(path, '0 HEAD\n1 SOUR CHANGED\n0 TRLR\n')

    await expect(broker.resolveReadGrant(owner, grant!.grantId, 'gedcom-read')).rejects.toMatchObject({ code: 'FILE_GRANT_STALE' })
  })

  it('copies a granted input once into immutable private staging without returning a host path', async () => {
    const root = await temporaryRoot()
    const source = join(root, 'source.ged')
    const staging = join(root, 'private-staging')
    const destination = join(staging, 'input.ged')
    const content = '0 HEAD\n1 SOUR STAGED\n0 TRLR\n'
    await writeFile(source, content)
    await mkdir(staging, { mode: 0o700 })
    const broker = new FileGrantBroker(dialogs({ selectOpenFile: vi.fn().mockResolvedValue(source) }))
    const owner = {}
    const grant = await broker.requestOpenGrant(owner, { purpose: 'gedcom-read' })

    const staged = await broker.stageReadGrant(owner, grant!.grantId, 'gedcom-read', destination)

    expect(staged).toEqual({
      grantId: grant!.grantId,
      purpose: 'gedcom-read',
      sizeBytes: Buffer.byteLength(content),
      sha256: '8833aa9eb59feec44ff56ecc6375c4685b044f99533b167996d5f326c635bc0a',
    })
    expect(JSON.stringify(staged)).not.toContain(root)
    await expect(readFile(destination, 'utf8')).resolves.toBe(content)
    if (process.platform !== 'win32') {
      expect((await lstat(destination)).mode & 0o777).toBe(0o400)
    }
    await expect(broker.stageReadGrant(owner, grant!.grantId, 'gedcom-read', join(staging, 'again.ged')))
      .rejects.toMatchObject({ code: 'FILE_GRANT_REVOKED' })
  })

  it('permits exactly one concurrent redemption of a single-use grant', async () => {
    const root = await temporaryRoot()
    const path = join(root, 'single-use.ged')
    await writeFile(path, '0 HEAD\n0 TRLR\n')
    const broker = new FileGrantBroker(dialogs({ selectOpenFile: vi.fn().mockResolvedValue(path) }))
    const owner = {}
    const grant = await broker.requestOpenGrant(owner, { purpose: 'gedcom-read' })

    const outcomes = await Promise.allSettled([
      broker.resolveReadGrant(owner, grant!.grantId, 'gedcom-read'),
      broker.resolveReadGrant(owner, grant!.grantId, 'gedcom-read'),
    ])

    expect(outcomes.filter(({ status }) => status === 'fulfilled')).toHaveLength(1)
    expect(outcomes.filter(({ status }) => status === 'rejected')).toHaveLength(1)
    expect(outcomes.find(({ status }) => status === 'rejected')).toMatchObject({
      reason: { code: 'FILE_GRANT_REVOKED' },
    })
  })

  it('requires an explicit native confirmation for replacement and serializes output grants', async () => {
    const root = await temporaryRoot()
    const path = join(root, 'existing.ged')
    await writeFile(path, '0 HEAD\n0 TRLR\n')
    const rejectConfirmation = vi.fn().mockResolvedValue(false)
    const rejecting = new FileGrantBroker(dialogs({
      selectSaveFile: vi.fn().mockResolvedValue(path),
      confirmReplacement: rejectConfirmation,
    }))
    await expect(rejecting.requestSaveGrant({}, { purpose: 'gedcom-write', suggestedName: 'export.ged' })).resolves.toBeNull()
    expect(rejectConfirmation).toHaveBeenCalledWith(expect.anything(), 'existing.ged', undefined)

    const accepting = new FileGrantBroker(dialogs({
      selectSaveFile: vi.fn().mockResolvedValue(path),
      confirmReplacement: vi.fn().mockResolvedValue(true),
    }))
    const owner = {}
    const grant = await accepting.requestSaveGrant(owner, { purpose: 'gedcom-write', suggestedName: 'export.ged' })
    expect(grant).toMatchObject({ metadata: { validation: 'replacement-confirmed' } })
    await expect(accepting.requestSaveGrant({}, { purpose: 'gedcom-write', suggestedName: 'other.ged' })).rejects.toMatchObject({ code: 'FILE_GRANT_CONFLICT' })
    await expect(accepting.resolveWriteGrant(owner, grant!.grantId, 'gedcom-write')).resolves.toMatchObject({ path })
    await expect(accepting.resolveWriteGrant(owner, grant!.grantId, 'gedcom-write')).rejects.toMatchObject({ code: 'FILE_GRANT_REVOKED' })
    accepting.revokeGrant(owner, grant!.grantId)
  })

  it('rejects replacement and new-output races without changing either target', async () => {
    const root = await temporaryRoot()
    const existing = join(root, 'existing.ged')
    const original = '0 HEAD\n1 SOUR ORIGINAL\n0 TRLR\n'
    const replacement = '0 HEAD\n1 SOUR REPLACED\n0 TRLR\n'
    await writeFile(existing, original)
    const replacementBroker = new FileGrantBroker(dialogs({
      selectSaveFile: vi.fn().mockResolvedValue(existing),
      confirmReplacement: vi.fn().mockImplementation(async () => {
        await writeFile(existing, replacement)
        return true
      }),
    }))
    await expect(replacementBroker.requestSaveGrant({}, {
      purpose: 'gedcom-write',
      suggestedName: 'existing.ged',
    })).rejects.toMatchObject({ code: 'FILE_GRANT_STALE' })
    const { readFile } = await import('node:fs/promises')
    await expect(readFile(existing, 'utf8')).resolves.toBe(replacement)

    const newTarget = join(root, 'new.ged')
    const owner = {}
    const newBroker = new FileGrantBroker(dialogs({
      selectSaveFile: vi.fn().mockResolvedValue(newTarget),
    }))
    const grant = await newBroker.requestSaveGrant(owner, {
      purpose: 'gedcom-write',
      suggestedName: 'new.ged',
    })
    await writeFile(newTarget, original)
    await expect(newBroker.resolveWriteGrant(owner, grant!.grantId, 'gedcom-write'))
      .rejects.toMatchObject({ code: 'FILE_GRANT_STALE' })
    await expect(readFile(newTarget, 'utf8')).resolves.toBe(original)
    await expect(readdir(root)).resolves.toEqual(expect.arrayContaining(['existing.ged', 'new.ged']))
  })

  it('validates and atomically publishes a staged output without returning a host path', async () => {
    const root = await temporaryRoot()
    const privateRoot = join(root, 'private-staging')
    const stagedPath = join(privateRoot, 'result.ged')
    const target = join(root, 'published.ged')
    const content = '0 HEAD\n1 SOUR PUBLISHED\n0 TRLR\n'
    await mkdir(privateRoot, { mode: 0o700 })
    await writeFile(stagedPath, content, { mode: 0o600 })
    const broker = new FileGrantBroker(dialogs({
      selectSaveFile: vi.fn().mockResolvedValue(target),
      confirmReplacement: vi.fn().mockResolvedValue(true),
    }))
    const owner = {}
    const grant = await broker.requestSaveGrant(owner, {
      purpose: 'gedcom-write',
      suggestedName: 'published.ged',
    })

    const published = await broker.publishWriteGrant(owner, grant!.grantId, 'gedcom-write', stagedPath)

    expect(published).toEqual({
      grantId: grant!.grantId,
      purpose: 'gedcom-write',
      sizeBytes: Buffer.byteLength(content),
      sha256: '770ca9286b8db4fc205c0dd3efca1e122da29f5c767232e6ab9f1d969262aad0',
      durability: 'confirmed',
    })
    expect(JSON.stringify(published)).not.toContain(root)
    await expect(readFile(target, 'utf8')).resolves.toBe(content)
    expect((await readdir(root)).filter((name) => name.includes('.ancestryllm-'))).toEqual([])
    await expect(broker.requestSaveGrant({}, {
      purpose: 'gedcom-write',
      suggestedName: 'published.ged',
    })).resolves.toMatchObject({ metadata: { validation: 'replacement-confirmed' } })
  })

  it('preserves a committed output when directory durability cannot be confirmed', async () => {
    const root = await temporaryRoot()
    const privateRoot = join(root, 'private-staging')
    const stagedPath = join(privateRoot, 'result.ged')
    const target = join(root, 'published.ged')
    const content = '0 HEAD\n1 SOUR DURABILITY\n0 TRLR\n'
    await mkdir(privateRoot, { mode: 0o700 })
    await writeFile(stagedPath, content, { mode: 0o600 })
    const publication: FilePublicationPort = {
      rename,
      syncDirectory: vi.fn(async () => { throw new Error('directory sync failed') }),
    }
    const broker = new FileGrantBroker(dialogs({
      selectSaveFile: vi.fn().mockResolvedValue(target),
    }), publication)
    const owner = {}
    const grant = await broker.requestSaveGrant(owner, {
      purpose: 'gedcom-write',
      suggestedName: 'published.ged',
    })

    const published = await broker.publishWriteGrant(owner, grant!.grantId, 'gedcom-write', stagedPath)

    expect(published).toMatchObject({
      grantId: grant!.grantId,
      purpose: 'gedcom-write',
      durability: 'unconfirmed',
    })
    await expect(readFile(target, 'utf8')).resolves.toBe(content)
    expect((await readdir(root)).filter((name) => name.includes('.ancestryllm-'))).toEqual([])
  })

  it('preserves an existing output and releases its lock when staged validation fails', async () => {
    const root = await temporaryRoot()
    const privateRoot = join(root, 'private-staging')
    const stagedPath = join(privateRoot, 'invalid.md')
    const target = join(root, 'existing.md')
    const original = '# Original report\n'
    await mkdir(privateRoot, { mode: 0o700 })
    await writeFile(stagedPath, Buffer.from('BZh9malformed-archive', 'ascii'))
    await writeFile(target, original)
    const broker = new FileGrantBroker(dialogs({
      selectSaveFile: vi.fn().mockResolvedValue(target),
      confirmReplacement: vi.fn().mockResolvedValue(true),
    }))
    const owner = {}
    const grant = await broker.requestSaveGrant(owner, {
      purpose: 'markdown-write',
      suggestedName: 'existing.md',
    })

    await expect(broker.publishWriteGrant(owner, grant!.grantId, 'markdown-write', stagedPath))
      .rejects.toMatchObject({ code: 'FILE_SELECTION_INVALID' })

    await expect(readFile(target, 'utf8')).resolves.toBe(original)
    await expect(broker.requestSaveGrant({}, {
      purpose: 'markdown-write',
      suggestedName: 'existing.md',
    })).resolves.toMatchObject({ metadata: { validation: 'replacement-confirmed' } })
  })

  it('rejects an oversized sparse staged output before publication', async () => {
    const root = await temporaryRoot()
    const privateRoot = join(root, 'private-staging')
    const stagedPath = join(privateRoot, 'oversized.md')
    await mkdir(privateRoot, { mode: 0o700 })
    await writeFile(stagedPath, '# Fictional report\n', { mode: 0o600 })
    await truncate(stagedPath, 67_108_865)
    const broker = new FileGrantBroker(dialogs())

    await expect(broker.validateStagedOutput('markdown-write', stagedPath))
      .rejects.toMatchObject({ code: 'FILE_TOO_LARGE' })
  })

  it('cancels publication without changing its target or leaving temporary files', async () => {
    const root = await temporaryRoot()
    const privateRoot = join(root, 'private-staging')
    const stagedPath = join(privateRoot, 'result.ged')
    const target = join(root, 'existing.ged')
    const original = '0 HEAD\n1 SOUR ORIGINAL\n0 TRLR\n'
    await mkdir(privateRoot, { mode: 0o700 })
    await writeFile(stagedPath, '0 HEAD\n1 SOUR CANCELLED\n0 TRLR\n')
    await writeFile(target, original)
    const broker = new FileGrantBroker(dialogs({
      selectSaveFile: vi.fn().mockResolvedValue(target),
      confirmReplacement: vi.fn().mockResolvedValue(true),
    }))
    const owner = {}
    const grant = await broker.requestSaveGrant(owner, {
      purpose: 'gedcom-write',
      suggestedName: 'existing.ged',
    })
    const controller = new AbortController()
    controller.abort(new Error('/private/path-must-not-leak'))

    await expect(broker.publishWriteGrant(owner, grant!.grantId, 'gedcom-write', stagedPath, controller.signal))
      .rejects.toMatchObject({ code: 'FILE_OPERATION_CANCELLED', message: 'FILE_OPERATION_CANCELLED' })

    await expect(readFile(target, 'utf8')).resolves.toBe(original)
    expect((await readdir(root)).filter((name) => name.includes('.ancestryllm-'))).toEqual([])
    await expect(broker.requestSaveGrant({}, {
      purpose: 'gedcom-write',
      suggestedName: 'existing.ged',
    })).resolves.toMatchObject({ metadata: { validation: 'replacement-confirmed' } })
  })

  it('rejects an output grant when its selected parent directory is replaced', async () => {
    const root = await temporaryRoot()
    const parent = join(root, 'selected-output')
    const displacedParent = join(root, 'displaced-output')
    const path = join(parent, 'new.ged')
    await mkdir(parent)
    const owner = {}
    const broker = new FileGrantBroker(dialogs({
      selectSaveFile: vi.fn().mockResolvedValue(path),
    }))
    const grant = await broker.requestSaveGrant(owner, {
      purpose: 'gedcom-write',
      suggestedName: 'new.ged',
    })

    await rename(parent, displacedParent)
    await mkdir(parent)

    await expect(broker.resolveWriteGrant(owner, grant!.grantId, 'gedcom-write'))
      .rejects.toMatchObject({ code: 'FILE_GRANT_STALE' })
    await expect(readdir(parent)).resolves.toEqual([])
    await expect(readdir(displacedParent)).resolves.toEqual([])
  })

  it('rejects replacement-directory bytes if its selected parent moves during commit', async () => {
    const root = await temporaryRoot()
    const privateRoot = join(root, 'private-staging')
    const parent = join(root, 'selected-output')
    const displacedParent = join(root, 'displaced-output')
    const stagedPath = join(privateRoot, 'result.ged')
    const target = join(parent, 'new.ged')
    await mkdir(privateRoot, { mode: 0o700 })
    await mkdir(parent)
    await writeFile(stagedPath, '0 HEAD\n1 SOUR PARENT-RACE\n0 TRLR\n', { mode: 0o600 })
    const publication: FilePublicationPort = {
      rename: vi.fn(async (source, destination) => {
        await rename(parent, displacedParent)
        await mkdir(parent)
        await writeFile(join(parent, basename(source)), 'attacker-controlled replacement')
        await rename(source, destination)
      }),
      syncDirectory: vi.fn(async () => undefined),
    }
    const broker = new FileGrantBroker(dialogs({
      selectSaveFile: vi.fn().mockResolvedValue(target),
    }), publication)
    const owner = {}
    const grant = await broker.requestSaveGrant(owner, {
      purpose: 'gedcom-write',
      suggestedName: 'new.ged',
    })

    await expect(broker.publishWriteGrant(owner, grant!.grantId, 'gedcom-write', stagedPath))
      .rejects.toMatchObject({ code: 'FILE_SELECTION_INVALID' })

    await expect(readdir(parent)).resolves.toEqual(['new.ged'])
    await expect(readdir(displacedParent)).resolves.toEqual([])
    await expect(readFile(target, 'utf8')).resolves.toBe('attacker-controlled replacement')
  })

  it('cancels native dialogs without creating grants or touching existing output', async () => {
    const root = await temporaryRoot()
    const existing = join(root, 'sentinel.ged')
    const sentinel = 'do not replace'
    await writeFile(existing, sentinel)
    const confirmReplacement = vi.fn()
    const broker = new FileGrantBroker(dialogs({ confirmReplacement }))
    const before = await readdir(root)

    await expect(broker.requestOpenGrant({}, { purpose: 'gedcom-read' })).resolves.toBeNull()
    await expect(broker.requestSaveGrant({}, {
      purpose: 'gedcom-write',
      suggestedName: 'sentinel.ged',
    })).resolves.toBeNull()

    const { readFile } = await import('node:fs/promises')
    await expect(readFile(existing, 'utf8')).resolves.toBe(sentinel)
    await expect(readdir(root)).resolves.toEqual(before)
    expect(confirmReplacement).not.toHaveBeenCalled()
  })

  it('releases an output lock when its window closes', async () => {
    const root = await temporaryRoot()
    const path = join(root, 'released.ged')
    const broker = new FileGrantBroker(dialogs({
      selectSaveFile: vi.fn().mockResolvedValue(path),
    }))
    const firstOwner = {}
    await expect(broker.requestSaveGrant(firstOwner, {
      purpose: 'gedcom-write',
      suggestedName: 'released.ged',
    })).resolves.toMatchObject({ metadata: { validation: 'new-output' } })

    broker.revokeOwner(firstOwner)

    await expect(broker.requestSaveGrant({}, {
      purpose: 'gedcom-write',
      suggestedName: 'released.ged',
    })).resolves.toMatchObject({ metadata: { validation: 'new-output' } })
  })

  it('prevents a selected input from being aliased as an output', async () => {
    const root = await temporaryRoot()
    const path = join(root, 'same.ged')
    await writeFile(path, '0 HEAD\n0 TRLR\n')
    const broker = new FileGrantBroker(dialogs({
      selectOpenFile: vi.fn().mockResolvedValue(path),
      selectSaveFile: vi.fn().mockResolvedValue(path),
      confirmReplacement: vi.fn().mockResolvedValue(true),
    }))
    await broker.requestOpenGrant({}, { purpose: 'gedcom-read' })

    await expect(broker.requestSaveGrant({}, { purpose: 'gedcom-write', suggestedName: 'same.ged' })).rejects.toMatchObject({ code: 'FILE_GRANT_CONFLICT' })
  })

  it('serializes case-folded and Unicode-normalized output aliases', async () => {
    const root = await temporaryRoot()
    const selections = [
      join(root, 'Export.ged'),
      join(root, 'export.ged'),
      join(root, 'cafe\u0301.ged'),
      join(root, 'caf\u00e9.ged'),
    ]
    const broker = new FileGrantBroker(dialogs({
      selectSaveFile: vi.fn()
        .mockResolvedValueOnce(selections[0])
        .mockResolvedValueOnce(selections[1])
        .mockResolvedValueOnce(selections[2])
        .mockResolvedValueOnce(selections[3]),
    }))

    await expect(broker.requestSaveGrant({}, {
      purpose: 'gedcom-write',
      suggestedName: 'Export.ged',
    })).resolves.toBeTruthy()
    await expect(broker.requestSaveGrant({}, {
      purpose: 'gedcom-write',
      suggestedName: 'export.ged',
    })).rejects.toMatchObject({ code: 'FILE_GRANT_CONFLICT' })
    await expect(broker.requestSaveGrant({}, {
      purpose: 'gedcom-write',
      suggestedName: 'cafe\u0301.ged',
    })).resolves.toBeTruthy()
    await expect(broker.requestSaveGrant({}, {
      purpose: 'gedcom-write',
      suggestedName: 'caf\u00e9.ged',
    })).rejects.toMatchObject({ code: 'FILE_GRANT_CONFLICT' })
  })

  it('revokes every grant owned by a closed window without exposing existence', async () => {
    const root = await temporaryRoot()
    const path = join(root, 'owned.ged')
    await writeFile(path, '0 HEAD\n0 TRLR\n')
    const broker = new FileGrantBroker(dialogs({ selectOpenFile: vi.fn().mockResolvedValue(path) }))
    const owner = {}
    const grant = await broker.requestOpenGrant(owner, { purpose: 'gedcom-read' })

    broker.revokeOwner(owner)
    expect(broker.revokeGrant(owner, 'grt_' + 'a'.repeat(64))).toEqual({ revoked: true })
    await expect(broker.resolveReadGrant(owner, grant!.grantId, 'gedcom-read')).rejects.toBeInstanceOf(FileGrantBrokerError)
    await expect(broker.resolveReadGrant(owner, grant!.grantId, 'gedcom-read')).rejects.toMatchObject({ code: 'FILE_GRANT_REVOKED' })
  })

  it('cannot issue a grant after its owner is revoked while a native dialog is pending', async () => {
    const root = await temporaryRoot()
    const path = join(root, 'pending.ged')
    await writeFile(path, '0 HEAD\n0 TRLR\n')
    let completeDialog!: (value: string) => void
    const pendingDialog = new Promise<string>((resolve) => { completeDialog = resolve })
    const selectOpenFile = vi.fn()
      .mockReturnValueOnce(pendingDialog)
      .mockResolvedValueOnce(path)
    const broker = new FileGrantBroker(dialogs({ selectOpenFile }))
    const owner = {}
    const request = broker.requestOpenGrant(owner, { purpose: 'gedcom-read' })
    await vi.waitFor(() => expect(selectOpenFile).toHaveBeenCalledTimes(1))

    broker.revokeOwner(owner)
    completeDialog(path)

    await expect(request).rejects.toMatchObject({ code: 'FILE_GRANT_REVOKED' })
    await expect(broker.requestOpenGrant(owner, { purpose: 'gedcom-read' })).resolves.toMatchObject({
      metadata: { displayName: 'pending.ged' },
    })
  })
})
