import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'
import { discoverPackage } from './package-paths.mjs'

const releaseRoot = process.argv[2]
  ? resolve(process.argv[2])
  : fileURLToPath(new URL('../release/', import.meta.url))
const { executable } = await discoverPackage(releaseRoot)
process.stdout.write(executable)
