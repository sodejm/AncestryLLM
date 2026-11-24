/** Defines blocking lint rules for the desktop TypeScript and React workspace. */
import js from '@eslint/js'
import globals from 'globals'
import jsdoc from 'eslint-plugin-jsdoc'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

/** Enforces the shared desktop lint, React, TypeScript, and declaration-documentation policy. */
export default tseslint.config(
  { ignores: ['out/**', 'release/**', 'node_modules/**', 'sbom.cdx.json'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{js,mjs,ts,tsx}'],
    plugins: { jsdoc },
    settings: { jsdoc: { mode: 'typescript' } },
    rules: {
      'jsdoc/check-syntax': 'error',
      'jsdoc/check-tag-names': 'error',
      'jsdoc/require-description': 'error',
    },
  },
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
      // The desktop renderer does not opt into React Compiler. Its bridge initialization and
      // subscription effects intentionally synchronize local asynchronous state and refs.
      // Preserve the established Hooks correctness rules without claiming compiler compatibility.
      'react-hooks/refs': 'off',
      'react-hooks/set-state-in-effect': 'off',
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      'no-restricted-imports': ['error', { patterns: ['node:*', 'electron', 'node_modules/*'] }],
    },
  },
  { files: ['src/main/**/*.ts', 'src/preload/**/*.ts', 'scripts/**/*.mjs'], languageOptions: { globals: globals.node } },
  { files: ['**/*.test.{ts,tsx}'], languageOptions: { globals: globals.browser } },
)
