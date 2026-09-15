import js from '@eslint/js';
import { defineConfig, globalIgnores } from 'eslint/config';
import prettier from 'eslint-config-prettier';
import svelte from 'eslint-plugin-svelte';
import globals from 'globals';
import ts from 'typescript-eslint';

/**
 * Type-aware linting is deliberately not enabled: `tsc` and `svelte-check`
 * already run in strict mode over the same files, so a second type graph here
 * would double the cost for no extra signal.
 */
export default defineConfig([
  globalIgnores([
    '**/node_modules/**',
    '**/dist/**',
    '**/target/**',
    '**/.venv/**',
    '**/__pycache__/**',
    'apps/desktop/src-tauri/gen/**',
    'packages/protocol/fixtures/**',
  ]),
  js.configs.recommended,
  ts.configs.recommended,
  svelte.configs['flat/recommended'],
  {
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: 'module',
      globals: { ...globals.browser, ...globals.node },
    },
    rules: {
      eqeqeq: ['error', 'always'],
      'no-console': ['error', { allow: ['warn', 'error'] }],
      'prefer-const': 'error',
      '@typescript-eslint/consistent-type-imports': ['error', { prefer: 'type-imports' }],
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },
  {
    files: ['**/*.svelte', '**/*.svelte.ts'],
    languageOptions: { parserOptions: { parser: ts.parser } },
    rules: {
      // `$props()` destructuring must bind with `let`, so the core rule is
      // wrong here. The Svelte rule understands runes and excludes them.
      'prefer-const': 'off',
      'svelte/prefer-const': ['error', { excludedRunes: ['$props', '$derived'] }],
    },
  },
  {
    // Tests legitimately construct malformed values to prove they are rejected.
    files: ['**/test/**/*.ts', '**/*.test.ts'],
    rules: { '@typescript-eslint/no-explicit-any': 'off' },
  },
  svelte.configs['flat/prettier'],
  prettier,
]);
