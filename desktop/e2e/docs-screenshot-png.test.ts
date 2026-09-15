/** Verifies publication density metadata preserves the captured PNG pixels. */
import { crc32 } from 'node:zlib'
import { expect, test } from 'vitest'
import { withPublicationDensity } from './docs-screenshot-capture'

const png = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=',
  'base64',
)

test('adds one valid 144-dpi chunk without altering any original chunks', () => {
  const result = withPublicationDensity(png)
  const chunk = result.subarray(33, 54)
  expect(chunk.readUInt32BE(0)).toBe(9)
  expect(chunk.toString('ascii', 4, 8)).toBe('pHYs')
  expect(chunk.readUInt32BE(8)).toBe(5669)
  expect(chunk.readUInt32BE(12)).toBe(5669)
  expect(chunk[16]).toBe(1)
  expect(chunk.readUInt32BE(17)).toBe(crc32(chunk.subarray(4, 17)))
  expect(Buffer.concat([result.subarray(0, 33), result.subarray(54)])).toEqual(png)
  expect(withPublicationDensity(result)).toEqual(result)
})

test('replaces stale density rather than producing conflicting metadata', () => {
  const stale = withPublicationDensity(png)
  stale.writeUInt32BE(2835, 41)
  stale.writeUInt32BE(2835, 45)
  stale.writeUInt32BE(crc32(stale.subarray(37, 50)), 50)
  expect(withPublicationDensity(stale)).toEqual(withPublicationDensity(png))
})

test.each([Buffer.from('not a PNG'), png.subarray(0, 40)])(
  'rejects invalid or truncated capture bytes', (content) => {
    expect(() => withPublicationDensity(content)).toThrow('DOCSHOT_PNG_INVALID')
  },
)
