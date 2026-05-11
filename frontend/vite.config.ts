import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [sveltekit()],
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
