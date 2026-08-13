import { createHash } from 'node:crypto'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'

const lifecycle = process.env.npm_lifecycle_event ?? ''
const fixtureBuild = ['dev', 'dev:gallery', 'build:e2e'].includes(lifecycle)
const packagedFileGrantBuild = lifecycle === 'build:packaged-file-grants'
const packagedNativeVerificationBuild = [
  'build:packaged-native-verification',
  'build:packaged-file-grants',
].includes(lifecycle)
const mainAliases = {
  ...(fixtureBuild
    ? { './runtime-bridge': resolve('src/main/runtime-bridge.fixture.ts') }
    : {}),
  ...(packagedFileGrantBuild
    ? { './native-file-dialogs': resolve('e2e/native-file-dialogs.packaged-verification.ts') }
    : {}),
  ...(packagedNativeVerificationBuild
    ? { './native-verification': resolve('e2e/native-verification.packaged-verification.ts') }
    : {}),
}
const sidecarManifest = resolve(
  'build',
  'sidecar',
  `${process.platform}-${process.arch}`,
  'sidecar-manifest.json',
)
const sidecarManifestSha256 = existsSync(sidecarManifest)
  ? createHash('sha256').update(readFileSync(sidecarManifest)).digest('hex')
  : null

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    define: {
      __ANCESTRYLLM_SIDECAR_MANIFEST_SHA256__: JSON.stringify(sidecarManifestSha256),
    },
    ...(Object.keys(mainAliases).length > 0
      ? { resolve: { alias: mainAliases } }
      : {}),
    build: { sourcemap: false },
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      sourcemap: false,
      rollupOptions: {
        output: { entryFileNames: '[name].cjs', format: 'cjs' },
      },
    },
  },
  renderer: {
    root: resolve('src/renderer'),
    plugins: [react(), tailwindcss()],
    build: {
      sourcemap: false,
      assetsInlineLimit: 0,
      cssCodeSplit: false,
      rollupOptions: {
        output: {
          entryFileNames: 'assets/index.js',
          chunkFileNames: 'assets/[name].js',
          assetFileNames: (assetInfo) => assetInfo.names.some((name) => name.endsWith('.css'))
            ? 'assets/index.css'
            : 'assets/[name][extname]',
        },
      },
    },
  },
})
