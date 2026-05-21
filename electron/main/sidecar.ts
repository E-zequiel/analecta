import { spawn, ChildProcess } from 'child_process';
import path from 'path';
import { app, BrowserWindow } from 'electron';
import { setVaultPath } from './vault-state.js';

let sidecarProcess: ChildProcess | null = null;
let cachedPort: number | null = null;
const portResolvers: Array<(port: number) => void> = [];

export function getSidecarPort(): Promise<number> {
  if (cachedPort !== null) return Promise.resolve(cachedPort);
  return new Promise(resolve => portResolvers.push(resolve));
}

function getSidecarBinary(): string {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'analecta-sidecar', 'analecta-sidecar');
  }
  // In dev, __dirname is electron/dist/main/ — repo root is three levels up.
  const repoRoot = path.join(__dirname, '..', '..', '..');
  return path.join(repoRoot, 'binaries', 'analecta-sidecar', 'analecta-sidecar');
}

export function spawnSidecar(): void {
  const bin = getSidecarBinary();
  sidecarProcess = spawn(bin, [], { stdio: 'pipe' });

  sidecarProcess.stdout?.on('data', (chunk: Buffer) => {
    for (const line of chunk.toString().split('\n')) {
      const trimmed = line.trim();
      if (trimmed.startsWith('LISTENING_ON_PORT:')) {
        cachedPort = parseInt(trimmed.slice('LISTENING_ON_PORT:'.length), 10);
        for (const resolve of portResolvers) resolve(cachedPort);
        portResolvers.length = 0;
      } else if (trimmed.startsWith('VAULT_PATH:')) {
        setVaultPath(trimmed.slice('VAULT_PATH:'.length));
      } else if (trimmed === 'SIDECAR_READY') {
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
