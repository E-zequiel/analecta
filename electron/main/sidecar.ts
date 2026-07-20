import { spawn, ChildProcess } from 'child_process';
import path from 'path';
import { app, BrowserWindow } from 'electron';
import { setVaultPath } from './vault-state.js';
import { CHROME_MAJOR } from './chrome-identity.js';

let sidecarProcess: ChildProcess | null = null;
let cachedPort: number | null = null;
let sidecarReady = false;
const portResolvers: Array<(port: number) => void> = [];

/** Resolves only after SIDECAR_READY — guarantees HTTP is accepting connections. */
export function getSidecarPort(): Promise<number> {
	if (sidecarReady && cachedPort !== null) return Promise.resolve(cachedPort);
	return new Promise((resolve) => portResolvers.push(resolve));
}

function getSidecarBinary(): string {
	if (app.isPackaged) {
		return path.join(process.resourcesPath, 'analecta-sidecar', 'analecta-sidecar');
	}
	// In dev, __dirname is electron/dist/main/ — repo root is three levels up.
	const repoRoot = path.join(__dirname, '..', '..', '..');
	return path.join(repoRoot, 'binaries', 'analecta-sidecar', 'analecta-sidecar');
}

export function spawnSidecar(renderPort: number, renderToken: string): void {
	const bin = getSidecarBinary();
	sidecarProcess = spawn(bin, [], {
		stdio: 'pipe',
		env: {
			...process.env,
			ANALECTA_RENDER_PORT: String(renderPort),
			ANALECTA_RENDER_TOKEN: renderToken,
			// Single-sources the Chrome identity Tier 1 headers present with
			// Electron's own bundled Chromium — see chrome-identity.ts.
			ANALECTA_CHROME_MAJOR: CHROME_MAJOR,
		},
	});

	sidecarProcess.stdout?.on('data', (chunk: Buffer) => {
		for (const line of chunk.toString().split('\n')) {
			const trimmed = line.trim();
			if (trimmed.startsWith('LISTENING_ON_PORT:')) {
				cachedPort = parseInt(trimmed.slice('LISTENING_ON_PORT:'.length), 10);
				// Port is cached but resolvers wait until SIDECAR_READY.
			} else if (trimmed.startsWith('VAULT_PATH:')) {
				setVaultPath(trimmed.slice('VAULT_PATH:'.length));
			} else if (trimmed === 'SIDECAR_READY') {
				sidecarReady = true;
				for (const resolve of portResolvers) resolve(cachedPort!);
				portResolvers.length = 0;
				for (const win of BrowserWindow.getAllWindows()) {
					win.webContents.send('sidecar-ready', cachedPort);
				}
			}
		}
	});

	sidecarProcess.stderr?.on('data', (chunk: Buffer) => {
		for (const line of chunk.toString().split('\n')) {
			if (line.trim()) console.error('[sidecar]', line.trim());
		}
	});

	sidecarProcess.on('exit', (code, signal) => {
		console.log(`[sidecar] exited: code=${code} signal=${signal}`);
	});
}

export function killSidecar(): void {
	if (!sidecarProcess) return;
	const proc = sidecarProcess;
	sidecarProcess = null;
	proc.kill('SIGTERM');
	const fallback = setTimeout(() => proc.kill('SIGKILL'), 3000);
	proc.once('exit', () => clearTimeout(fallback));
}
