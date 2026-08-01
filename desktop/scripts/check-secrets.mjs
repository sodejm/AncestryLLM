import { readdir, readFile } from 'node:fs/promises'
import { join } from 'node:path'
const patterns = [/AKIA[0-9A-Z]{16}/, /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/, /sk-[A-Za-z0-9_-]{20,}/]
async function walk(root) {
  const out = []
  for (const entry of await readdir(root, { withFileTypes: true })) {
    if (entry.isDirectory()) out.push(...await walk(join(root, entry.name)))
    else out.push(join(root, entry.name))
  }
  return out
}
for (const file of await walk(new URL('../src', import.meta.url).pathname)) {
  const value = await readFile(file, 'utf8'); if (patterns.some((pattern) => pattern.test(value))) throw new Error(`Potential secret in ${file}`)
}
console.log('Desktop source secret scan passed.')
