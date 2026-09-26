/** Exercises the real packaged RootsMagic renderer, broker, and native sidecar together. */
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile, readdir, writeFile } from 'node:fs/promises'
import { basename, join } from 'node:path'
import { $, browser } from '@wdio/globals'
import axe from 'axe-core'
import type { AncestryBridge } from '../src/shared-contract/desktop'
import { createRootsMagicFixture } from './rootsmagic-fixture'

async function activateWithKeyboard(selector: string, key = 'Enter'): Promise<void> {
  const control = await $(selector)
  await control.waitForEnabled()
  for (let attempt = 0; attempt < 100 && !(await control.isFocused()); attempt += 1) {
    await browser.keys('Tab')
  }
  assert.equal(await control.isFocused(), true, `${selector} must be reachable using Tab`)
  await browser.keys(key)
}

async function clickButton(label: string): Promise<void> {
  await activateWithKeyboard(`button=${label}`)
}

async function assertAccessible(): Promise<void> {
  await browser.execute(axe.source)
  const violations = await browser.execute(async () => {
    const runner = (globalThis as unknown as { axe: {
      run(context: Document, options: { runOnly: { type: 'tag'; values: string[] } }):
        Promise<{ violations: { id: string; impact: string | null }[] }>
    } }).axe
    const result = await runner.run(document, {
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] },
    })
    return result.violations.map(({ id, impact }) => ({ id, impact }))
  })
  assert.deepEqual(violations, [])
}

async function waitText(value: string): Promise<void> {
  await browser.waitUntil(async () => (await $('main').getText()).includes(value), {
    timeout: 30_000, timeoutMsg: `RootsMagic workspace did not show ${value}`,
  })
}

function digest(payload: Uint8Array): string {
  return createHash('sha256').update(payload).digest('hex')
}

describe('unpublished unpacked native package', () => {
  it('queries and exports an immutable RootsMagic source through the native workbench', async function () {
    if (process.env.ANCESTRYLLM_ROOTSMAGIC_VERIFICATION !== '1') this.skip()
    const source = process.env.ANCESTRYLLM_FILE_GRANT_OPEN_PATH
    const output = process.env.ANCESTRYLLM_ROOTSMAGIC_OUTPUT_PATH
    const evidence = process.env.ANCESTRYLLM_ROOTSMAGIC_EVIDENCE
    assert.ok(source && output && evidence, 'RootsMagic fixture, new output directory, and evidence paths are required')
    assert.equal(process.env.ANCESTRYLLM_PACKAGED_FILE_GRANT_VERIFICATION, '1')
    createRootsMagicFixture(source)
    const sourceDigest = digest(await readFile(source))
    await browser.waitUntil(async () => browser.execute(async () => {
      const bridge = (window as unknown as { ancestry: AncestryBridge }).ancestry
      const state = await bridge.getStartupDiagnostics()
      return state.ok && state.data.state === 'ready'
    }), { timeout: 30_000, timeoutMsg: 'Packaged native sidecar did not become ready' })
    const presets = await browser.execute(async () => {
      try {
        return JSON.stringify(await (window as unknown as { ancestry: AncestryBridge }).ancestry.getRootsMagicPresets())
      } catch (error) {
        return JSON.stringify({ ok: false, error: JSON.stringify(error), message: String(error) })
      }
    })
    assert.equal((JSON.parse(presets) as { ok: boolean }).ok, true, presets)
    await clickButton('Continue to Home')
    await activateWithKeyboard('a=RootsMagic')
    await waitText('RootsMagic workspace')
    await clickButton('Choose RootsMagic source')
    await waitText('Source active:')
    assert.ok((await $('main').getText()).includes(basename(source)))
    assert.equal((await $('main').getText()).includes(source), false)
    await clickButton('People')
    await waitText('25 rows shown for people.')
    await assertAccessible()
    await clickButton('Next page')
    await waitText('5 rows shown for people.')
    await clickButton('Previous page')
    await waitText('25 rows shown for people.')
    await clickButton('Select Alex Fictional')
    await clickButton('Family links')
    await waitText('2 rows shown for family links.')
    await clickButton('Events')
    await waitText('1 rows shown for events.')
    assert.ok((await $('table').getText()).includes('Fictional County'))
    await clickButton('Choose new export folder')
    await assertAccessible()
    await activateWithKeyboard('input[type="checkbox"]', 'Space')
    await clickButton('Export portable GEDCOM')
    await waitText('Export complete.')
    await assertAccessible()
    assert.deepEqual((await readdir(output)).sort(), ['manifest.json', 'report.md', 'tree.ged'])
    const gedcom = await readFile(join(output, 'tree.ged'), 'utf8')
    assert.ok(gedcom.includes('2 VERS 5.5.5'))
    assert.ok(gedcom.includes('Alex /Fictional/'))
    assert.ok(gedcom.includes('Blair /Fictional/'))
    assert.equal(gedcom.includes('Living /Fictional/'), false)
    assert.equal(gedcom.includes('Person4 /Fictional/'), false)
    const manifestText = await readFile(join(output, 'manifest.json'), 'utf8')
    const manifest = JSON.parse(manifestText) as { files: Record<string, { sha256: string; bytes: number }> }
    for (const filename of ['tree.ged', 'report.md']) {
      const payload: Uint8Array = await readFile(join(output, filename))
      assert.deepEqual(manifest.files[filename], { sha256: digest(payload), bytes: payload.length })
    }
    assert.equal(manifestText.includes(source), false)
    assert.equal((await readFile(join(output, 'report.md'), 'utf8')).includes(source), false)
    assert.equal(digest(await readFile(source)), sourceDigest)
    await clickButton('Reveal export folder')
    await waitText('revealed in your system file browser.')
    await activateWithKeyboard('a=Tasks')
    await waitText('Task activity')
    await activateWithKeyboard('a=RootsMagic')
    await waitText('Source active:')
    await clickButton('Discard active source')
    await waitText('The RootsMagic source was discarded.')
    await writeFile(evidence, `${JSON.stringify({
      schemaVersion: 1,
      kind: 'ancestryllm-packaged-rootsmagic-workbench',
      status: 'passed',
      target: `${process.platform}-${process.arch}`,
      verificationOnlyDialogAdapter: true,
      observations: {
        sourceInspection: true, peoplePaging: true, familyLinks: true, events: true,
        rootedPortableExport: true, livingExcluded: true, unrelatedExcluded: true,
        digestAgreement: true, sourceUnchanged: true, artifactReveal: true, sourceWorkspaceReset: true,
        keyboardWorkflow: true, automatedWcagChecks: true,
      },
    }, null, 2)}\n`, { flag: 'wx', mode: 0o600 })
  })
})
