import { ipcMain, dialog, shell, clipboard, Notification, app } from 'electron';
import fs from 'fs/promises';
import type { BrowserWindow } from 'electron';
import { assertVaultPath, assertExistsPath, setVaultPath } from './vault-state.js';
import { getSidecarPort } from './sidecar.js';
import { checkForUpdates, downloadUpdate, quitAndInstall } from './updater.js';

let initialDeepLink: string | null = null;
let mainWindow: BrowserWindow | null = null;

export function setInitialDeepLink(url: string): void {
  initialDeepLink = url;
}

export function setMainWindowRef(win: BrowserWindow): void {
  mainWindow = win;
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function withDialogTimeout<T>(promise: Promise<T>): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((_, reject) =>
      setTimeout(() => reject(new Error('dialog-unavailable')), 8000)
    ),
  ]);
}

export function registerIpcHandlers(): void {
  // Returns the cached port from sidecar stdout; awaits if not yet available.
  ipcMain.handle('get-sidecar-port', () => getSidecarPort());

  // Updates the vault scope used by all filesystem IPC handlers.
  ipcMain.handle('update-vault-scope', (_event, rawPath: unknown) => {
    if (typeof rawPath !== 'string' || !rawPath.trim()) throw new Error('invalid path');
    setVaultPath(rawPath);
  });

  // Reads a text file; path must be within the vault directory.
  ipcMain.handle('read-file', async (_event, rawPath: unknown) => {
    if (typeof rawPath !== 'string' || !rawPath.trim()) throw new Error('invalid path');
    assertVaultPath(rawPath);
    return fs.readFile(rawPath, 'utf-8');
  });

  // Writes a text file; path must be within the vault directory.
  ipcMain.handle('write-file', async (_event, args: unknown) => {
    if (!isObject(args)) throw new Error('invalid args');
    const { path: rawPath, data } = args;
    if (typeof rawPath !== 'string' || !rawPath.trim()) throw new Error('invalid path');
    if (typeof data !== 'string') throw new Error('invalid data');
    assertVaultPath(rawPath);
    await fs.writeFile(rawPath, data, 'utf-8');
  });

  // Returns true if the path exists; no vault restriction (used to check vault dir itself).
  ipcMain.handle('file-exists', async (_event, rawPath: unknown) => {
    if (typeof rawPath !== 'string' || !rawPath.trim()) throw new Error('invalid path');
    assertExistsPath(rawPath);
    try { await fs.access(rawPath); return true; } catch { return false; }
  });

  // Opens a native file-picker dialog. 8 s timeout mitigates FileChooser portal SIGSEGV on COSMIC.
  ipcMain.handle('open-dialog', async (_event, opts: unknown) => {
    if (!isObject(opts)) throw new Error('invalid opts');
    const result = await withDialogTimeout(
      dialog.showOpenDialog(mainWindow!, opts as Electron.OpenDialogOptions)
    );
    if (result.canceled || !result.filePaths.length) return null;
    return result.filePaths[0];
  });

  // Shows a message box; returns the index of the button pressed. Used for confirm() shim.
  ipcMain.handle('show-message-box', async (_event, opts: unknown) => {
    if (!isObject(opts)) throw new Error('invalid opts');
    const result = await withDialogTimeout(
      dialog.showMessageBox(mainWindow!, opts as unknown as Electron.MessageBoxOptions)
    );
    return result.response;
  });

  // Opens a URL in the default browser; http/https only.
  ipcMain.handle('open-url', async (_event, rawUrl: unknown) => {
    if (typeof rawUrl !== 'string') throw new Error('invalid url');
    let parsed: URL;
    try { parsed = new URL(rawUrl); } catch { throw new Error('invalid url'); }
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      throw new Error('blocked scheme');
    }
    await shell.openExternal(rawUrl);
  });

  // Opens the OS file manager at the given path.
  ipcMain.handle('reveal-in-dir', (_event, rawPath: unknown) => {
    if (typeof rawPath !== 'string' || !rawPath.trim()) throw new Error('invalid path');
    shell.showItemInFolder(rawPath);
  });

  ipcMain.handle('clipboard-read', () => clipboard.readText());

  // Shows a system notification.
  ipcMain.handle('notify', (_event, args: unknown) => {
    if (!isObject(args)) throw new Error('invalid args');
    const { title, body } = args;
    if (typeof title !== 'string' || typeof body !== 'string') throw new Error('invalid args');
    new Notification({ title, body }).show();
  });

  ipcMain.handle('check-update', () => checkForUpdates());
  ipcMain.handle('download-and-install-update', async () => {
    await downloadUpdate();
    quitAndInstall();
  });
  ipcMain.handle('relaunch', () => { app.relaunch(); app.exit(0); });

  ipcMain.handle('get-login-item', () => app.getLoginItemSettings().openAtLogin);

  ipcMain.handle('set-login-item', (_event, value: unknown) => {
    if (typeof value !== 'boolean') throw new Error('invalid value');
    app.setLoginItemSettings({ openAtLogin: value });
  });

  // Returns the URL from the first analecta:// deep-link received before the renderer was ready.
  ipcMain.handle('get-initial-deep-link', () => {
    const link = initialDeepLink;
    initialDeepLink = null;
    return link;
  });
}
