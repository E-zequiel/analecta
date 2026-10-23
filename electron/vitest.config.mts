// Electron main-process unit tests. A separate vitest project from the
// frontend's: main code imports 'electron' and 'electron-updater', which the
// test files mock — the frontend config (jsdom) does not apply here.
import { defineConfig } from 'vitest/config';

export default defineConfig({
	test: {
		environment: 'node',
		include: ['main/**/*.test.ts'],
	},
});
