// Standalone Vitest config — deliberately NOT merging vite.config.ts:
// SvelteKit 3 keeps its configuration inside the sveltekit() plugin, which
// spins up the whole framework. Unit tests here target plain modules and
// compiled .svelte components only. See odd/tasks/frontend-vitest-infra.md.
import { svelte, vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import { svelteTesting } from '@testing-library/svelte/vite';
import { defineConfig } from 'vitest/config';

export default defineConfig({
	plugins: [
		svelte({
			preprocess: vitePreprocess(),
			// Kit 3 keeps its configuration inside the sveltekit() plugin and
			// forbids svelte.config.js (config_file_unsupported) — disable the
			// plugin's config-file search to avoid its warning every run.
			configFile: false
		}),
		svelteTesting()
	],
	// Electron main-process tests are a separate vitest project
	// (electron/vitest.config.mts, run by the same check.sh step).
	test: {
		environment: 'jsdom',
		include: ['src/**/*.test.ts']
	}
});
