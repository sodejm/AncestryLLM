import { fileURLToPath } from 'node:url'
import { discoverPackage } from './package-paths.mjs'

const releaseRoot = fileURLToPath(new URL('../release/', import.meta.url))
const { executable } = await discoverPackage(releaseRoot)
process.stdout.write(executable)
