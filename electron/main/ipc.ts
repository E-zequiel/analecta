import { ipcMain, dialog, shell, clipboard, Notification, app } from 'electron';
import fs from 'fs/promises';
import type { BrowserWindow } from 'electron';
import { assertVaultPath, assertExistsPath, setVaultPath } from './vault-state.js';
import { getSidecarPort } from './sidecar.js';
import { checkForUpdates, downloadUpdate, quitAndInstall } from './updater.js';

let initialDeepLink: string | null = null;
let mainWindow: BrowserWindow | null = null;
let closeToTray = true;

export function getCloseToTray(): boolean {
	return closeToTray;
}

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
		new Promise<T>((_, reject) => setTimeout(() => reject(new Error('dialog-unavailable')), 30000)),
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
		try {
			await fs.access(rawPath);
			return true;
		} catch {
			return false;
		}
	});

	// Opens a native file-picker dialog. 30 s timeout covers the slow XDG FileChooser portal on GNOME/Wayland.
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
		try {
			parsed = new URL(rawUrl);
		} catch {
			throw new Error('invalid url');
		}
		if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
			throw new Error('blocked scheme');
		}
		await shell.openExternal(rawUrl);
	});

	// Opens the OS file manager at the given path; path must be within the vault directory.
	ipcMain.handle('reveal-in-dir', (_event, rawPath: unknown) => {
		if (typeof rawPath !== 'string' || !rawPath.trim()) throw new Error('invalid path');
		assertVaultPath(rawPath);
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

	ipcMain.handle('check-update', () => (app.isPackaged ? checkForUpdates() : Promise.resolve()));
	ipcMain.handle('download-and-install-update', async () => {
		if (!app.isPackaged) return;
		await downloadUpdate();
		quitAndInstall();
	});
	ipcMain.handle('relaunch', () => {
		app.relaunch();
		app.exit(0);
	});

	ipcMain.handle('get-login-item', () => app.getLoginItemSettings().openAtLogin);

	ipcMain.handle('set-login-item', (_event, value: unknown) => {
		if (typeof value !== 'boolean') throw new Error('invalid value');
		app.setLoginItemSettings({ openAtLogin: value });
	});

	ipcMain.handle('set-close-to-tray', (_event, value: unknown) => {
		if (typeof value !== 'boolean') throw new Error('invalid value');
		closeToTray = value;
	});

	// Returns the URL from the first analecta:// deep-link received before the renderer was ready.
	ipcMain.handle('get-initial-deep-link', () => {
		const link = initialDeepLink;
		initialDeepLink = null;
		return link;
	});

	ipcMain.handle('window-minimize', () => mainWindow?.minimize());
	ipcMain.handle('window-maximize', () => {
		if (mainWindow?.isMaximized()) mainWindow.unmaximize();
		else mainWindow?.maximize();
	});
	ipcMain.handle('window-close', () => mainWindow?.close());
	// startMoving/startResizing exist in Electron 28+/30+ runtime but are absent from the
	// electron@42.1.0 type definitions — cast through any until types are updated upstream.
	// eslint-disable-next-line @typescript-eslint/no-explicit-any, @typescript-eslint/no-unsafe-call, @typescript-eslint/no-unsafe-return, @typescript-eslint/no-unsafe-member-access
	ipcMain.handle('window-start-move', () => (mainWindow as any)?.startMoving());
	ipcMain.handle('window-start-resize', (_e, edge: unknown) => {
		const VALID = new Set([
			'top',
			'bottom',
			'left',
			'right',
			'top-left',
			'top-right',
			'bottom-left',
			'bottom-right',
		]);
		if (typeof edge === 'string' && VALID.has(edge))
			// eslint-disable-next-line @typescript-eslint/no-explicit-any, @typescript-eslint/no-unsafe-call, @typescript-eslint/no-unsafe-member-access
			(mainWindow as any)?.startResizing(edge);
	});
	ipcMain.handle('window-is-maximized', () => mainWindow?.isMaximized() ?? false);
}
