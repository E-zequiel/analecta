// @ts-check
import tseslint from 'typescript-eslint';
import sveltePlugin from 'eslint-plugin-svelte';
import eslintConfigPrettier from 'eslint-config-prettier';

export default tseslint.config(
  // ── Global ignores ────────────────────────────────────────────────────────
  {
    ignores: [
      '**/node_modules/**',
      '**/dist/**',
      '**/build/**',
      'frontend/.svelte-kit/**',
      'binaries/**',
      'scripts/**',
    ],
  },

  // ── Electron: TypeScript — type-checked rules ─────────────────────────────
  {
    files: ['electron/**/*.ts'],
    extends: tseslint.configs.recommendedTypeChecked,
    languageOptions: {
      parserOptions: {
        project: './electron/tsconfig.json',
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },

  // ── Frontend: TypeScript — type-checked rules ─────────────────────────────
  {
    files: ['frontend/src/**/*.ts'],
    extends: tseslint.configs.recommendedTypeChecked,
    languageOptions: {
      parserOptions: {
        project: './frontend/tsconfig.json',
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },

  // ── Frontend: Svelte — Svelte rules + TS recommended ─────────────────────
  // Type checking for Svelte component internals is handled by svelte-check.
  {
    files: ['frontend/src/**/*.svelte'],
    extends: [
      ...tseslint.configs.recommended,
      ...sveltePlugin.configs['flat/recommended'],
    ],
    languageOptions: {
      parserOptions: {
        parser: tseslint.parser,
        extraFileExtensions: ['.svelte'],
      },
    },
    rules: {
      // goto() and <a href> in regular event handlers / onMount don't require a
      // resolve() callback — that pattern is only for SvelteKit onNavigate hooks.
      'svelte/no-navigation-without-resolve': ['error', { ignoreGoto: true, ignoreLinks: true }],
      // Allow unused params prefixed with _ (e.g. intentionally unimplemented function params).
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },

  // ── Prettier: disable all formatting rules — must be last ─────────────────
  eslintConfigPrettier,
);
