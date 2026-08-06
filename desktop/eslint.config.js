/** Defines desktop ESLint rules, globals, and network-denial guardrails for the Electron scaffold. */
import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['out/**', 'release/**', 'node_modules/**', 'sbom.cdx.json'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['src/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-globals': ['error',
        { name: 'EventSource', message: 'The desktop scaffold must remain network-free.' },
        { name: 'fetch', message: 'The desktop scaffold must remain network-free.' },
        { name: 'WebSocket', message: 'The desktop scaffold must remain network-free.' },
        { name: 'XMLHttpRequest', message: 'The desktop scaffold must remain network-free.' },
      ],
    },
  },
  {
    files: ['src/renderer/**/*.{ts,tsx}'],
    languageOptions: { globals: globals.browser },
    plugins: { 'react-hooks': reactHooks, 'react-refresh': reactRefresh },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      'no-restricted-imports': ['error', { patterns: ['node:*', 'electron', 'node_modules/*'] }],
    },
  },
  { files: ['src/main/**/*.ts', 'src/preload/**/*.ts', 'scripts/**/*.mjs'], languageOptions: { globals: globals.node } },
  { files: ['**/*.test.{ts,tsx}'], languageOptions: { globals: globals.browser } },
)
