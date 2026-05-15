import { sveltekit } from '@sveltejs/kit/vite';
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
	plugins: [sveltekit(), svelteVirtualCssFallback()],
	clearScreen: false,
	// CodeMirror bundles ~502 kB (minified) into the editor route chunk — expected, see docs/quality-gate.md
	build: { chunkSizeWarningLimit: 600 },
	server: {
		port: 5173,
		strictPort: true,
		watch: {
			ignored: ['**/src-tauri/**']
		}
	}
});
