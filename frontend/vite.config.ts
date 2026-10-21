import adapterStatic from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import { defineConfig, type Plugin } from 'vite';

// vite-plugin-svelte 7 + Vite 8: when the compiled-css cache is cold,
// the load hook returns undefined and Vite falls back to reading the raw
// .svelte file, which PostCSS then fails to parse as CSS.
// This guard returns empty CSS for any unclaimed svelte virtual css module.
function svelteVirtualCssFallback(): Plugin {
	return {
		name: 'svelte-virtual-css-fallback',
		enforce: 'post',
		load(id) {
			if (/[?&]svelte&type=style&lang\.css/.test(id)) {
				return { code: '' };
			}
		}
	};
}

export default defineConfig({
	// SvelteKit 3 requires this configuration here — svelte.config.js is
	// no longer read (kit config_file_unsupported). See
	// https://svelte.dev/docs/kit/configuration.
	plugins: [
		sveltekit({
			preprocess: vitePreprocess(),
			adapter: adapterStatic({
				pages: 'build',
				assets: 'build',
				fallback: 'index.html',
				precompress: false,
				strict: false
			})
		}),
		svelteVirtualCssFallback()
	],
	clearScreen: false,
	// Two large shared chunks are expected — see docs/quality-gate.md:
	// · Shiki ("shiki-classes", ~1077 kB) — shared between viewer and editor
	// · CodeMirror (~500 kB) — editor route only
	build: { chunkSizeWarningLimit: 1200 },
	server: {
		port: 5173,
		strictPort: true
	}
});
