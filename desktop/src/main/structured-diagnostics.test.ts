/** Verifies the privacy-safe desktop diagnostic contract and bounded retention. */
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import {
  DESKTOP_DIAGNOSTIC_SCHEMA_VERSION,
  DesktopDiagnosticWriter,
  createDesktopDiagnosticEvent,
} from './structured-diagnostics'

const temporaryDirectories: string[] = []
const RUN_ID = '123e4567-e89b-42d3-a456-426614174000'

function temporaryDirectory(): string {
  const directory = mkdtempSync(join(tmpdir(), 'ancestryllm-diagnostics-'))
  temporaryDirectories.push(directory)
  return directory
}

afterEach(() => {
  for (const directory of temporaryDirectories.splice(0)) {
    rmSync(directory, { recursive: true, force: true })
  }
})

describe('structured desktop diagnostics', () => {
  it('creates the exact versioned contract with bounded numeric metadata', () => {
    expect(createDesktopDiagnosticEvent({
      runId: RUN_ID,
      appVersion: '0.7.0-dev.1',
      code: 'DESKTOP_STARTING',
      severity: 'info',
      component: 'electron-main',
      metadata: { retry_count: 2, degraded: false },
      now: new Date('2026-08-19T12:34:56.789Z'),
    })).toEqual({
      schema_version: DESKTOP_DIAGNOSTIC_SCHEMA_VERSION,
      timestamp: '2026-08-19T12:34:56.789Z',
      run_id: RUN_ID,
      code: 'DESKTOP_STARTING',
      severity: 'info',
      component: 'electron-main',
      app_version: '0.7.0-dev.1',
      metadata: { retry_count: 2, degraded: false },
    })
  })

  it.each([
    ['genealogy content', { family_name: 'Fictional Family' }],
    ['prompt text', { prompt: 'Summarize this family tree' }],
    ['raw path', { path: '/Users/canary/private-tree.rmtree' }],
    ['secret value', { token: 'canary-secret-token' }],
    ['free-form text', { detail: 'arbitrary detail' }],
  ])('rejects the %s privacy canary', (_label, metadata) => {
    expect(() => createDesktopDiagnosticEvent({
      runId: RUN_ID,
      appVersion: '0.7.0',
      code: 'PRIVACY_CANARY',
      severity: 'error',
      component: 'python-core',
      metadata: metadata as never,
    })).toThrow()
  })

  it.each([
    'not-a-uuid',
    '123e4567-e89b-12d3-a456-426614174000',
    '123E4567-E89B-42D3-A456-426614174000',
  ])('rejects the malformed launch correlation identifier %s', (runId) => {
    expect(() => createDesktopDiagnosticEvent({
      runId,
      appVersion: '0.7.0',
      code: 'MALFORMED_RUN_ID',
      severity: 'error',
      component: 'electron-main',
    })).toThrow('Invalid diagnostic run identifier')
  })

  it('refuses an event larger than the configured component file bound', () => {
    const directory = temporaryDirectory()
    const writer = new DesktopDiagnosticWriter({
      directory,
      runId: RUN_ID,
      appVersion: '0.7.0',
      component: 'electron-main',
      maxBytes: 32,
    })

    expect(writer.write('OVERSIZED_FOR_STREAM', 'warning')).toBe(false)
    expect(readdirSync(directory)).toEqual([])
  })

  it('never persists rejected privacy canaries', () => {
    const directory = temporaryDirectory()
    const writer = new DesktopDiagnosticWriter({
      directory,
      runId: RUN_ID,
      appVersion: '0.7.0',
      component: 'desktop-sidecar',
    })
    const canaries = [
      'Fictional Family',
      'Summarize this family tree',
      '/Users/canary/private-tree.rmtree',
      'canary-secret-token',
    ]

    expect(writer.write('PRIVACY_CANARY', 'error', { family_name: canaries[0] } as never)).toBe(false)
    expect(writer.write('PRIVACY_CANARY', 'error', { prompt: canaries[1] } as never)).toBe(false)
    expect(writer.write('PRIVACY_CANARY', 'error', { path: canaries[2] } as never)).toBe(false)
    expect(writer.write('PRIVACY_CANARY', 'error', { token: canaries[3] } as never)).toBe(false)
    expect(writer.write('PRIVACY_CHECK_COMPLETE', 'info')).toBe(true)

    const persisted = readdirSync(directory)
      .map((file) => readFileSync(join(directory, file), 'utf8'))
      .join('\n')
    for (const canary of canaries) expect(persisted).not.toContain(canary)
  })

  it('rotates component files within the configured byte and file bounds', () => {
    const directory = temporaryDirectory()
    const writer = new DesktopDiagnosticWriter({
      directory,
      runId: RUN_ID,
      appVersion: '0.7.0',
      component: 'electron-main',
      maxBytes: 330,
      maxFiles: 3,
    })

    for (let index = 0; index < 12; index += 1) {
      expect(writer.write('ROTATION_CHECK', 'info', { sequence: index })).toBe(true)
    }

    const files = readdirSync(directory).sort()
    expect(files).toEqual([
      'electron-main.jsonl',
      'electron-main.jsonl.1',
      'electron-main.jsonl.2',
    ])
    for (const file of files) {
      expect(Buffer.byteLength(readFileSync(join(directory, file)))).toBeLessThanOrEqual(330)
    }
    expect(files.join('\n')).not.toContain('Fictional Family')
  })

  it('refuses a symbolic-link diagnostics directory', () => {
    const parent = temporaryDirectory()
    const target = join(parent, 'target')
    const link = join(parent, 'diagnostics-link')
    mkdirSync(target)
    try {
      symlinkSync(target, link, 'dir')
    } catch {
      return
    }
    const writer = new DesktopDiagnosticWriter({
      directory: link,
      runId: RUN_ID,
      appVersion: '0.7.0',
      component: 'electron-main',
    })

    expect(writer.write('DESKTOP_STARTING', 'info')).toBe(false)
    expect(writer.clear()).toBe(false)
    expect(readdirSync(target)).toEqual([])
  })

  it('refuses a symbolic-link active file without changing its target', () => {
    const directory = temporaryDirectory()
    const target = join(directory, 'outside.jsonl')
    const active = join(directory, 'electron-main.jsonl')
    writeFileSync(target, 'untouched')
    try {
      symlinkSync(target, active, 'file')
    } catch {
      return
    }
    const writer = new DesktopDiagnosticWriter({
      directory,
      runId: RUN_ID,
      appVersion: '0.7.0',
      component: 'electron-main',
    })

    expect(writer.write('DESKTOP_STARTING', 'info')).toBe(false)
    expect(writer.clear()).toBe(false)
    expect(readFileSync(target, 'utf8')).toBe('untouched')
  })

  it('keeps validation and filesystem failures non-blocking', () => {
    const parent = temporaryDirectory()
    const invalidDirectory = join(parent, 'not-a-directory')
    writeFileSync(invalidDirectory, 'occupied')
    const writer = new DesktopDiagnosticWriter({
      directory: invalidDirectory,
      runId: RUN_ID,
      appVersion: '0.7.0',
      component: 'desktop-sidecar',
    })

    expect(writer.write('SIDECAR_STARTING', 'info')).toBe(false)
    expect(writer.write('bad code', 'info')).toBe(false)
    expect(writer.clear()).toBe(false)
  })
})
