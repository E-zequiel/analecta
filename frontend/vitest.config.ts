// Standalone Vitest config — deliberately NOT merging vite.config.ts:
// SvelteKit 3 keeps its configuration inside the sveltekit() plugin, which
// spins up the whole framework. Unit tests here target plain modules and
// compiled .svelte components only. See odd/tasks/frontend-vitest-infra.md.
import { svelte, vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import { svelteTesting } from '@testing-library/svelte/vite';
import { defineConfig } from 'vitest/config';

export default defineConfig({
	plugins: [svelte({ preprocess: vitePreprocess() }), svelteTesting()],
	test: {
		environment: 'jsdom',
		include: ['src/**/*.test.ts']
	}
});
