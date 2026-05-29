import { autoUpdater } from 'electron-updater';
import { app } from 'electron';
import type { BrowserWindow } from 'electron';

export function initUpdater(win: BrowserWindow): void {
	if (!app.isPackaged) return;
	autoUpdater.autoDownload = false;
	autoUpdater.on('update-available', (info) => {
		win.webContents.send('update-available', info);
	});
	autoUpdater.on('error', (err) => {
		console.error('[updater]', err);
	});
}

export function checkForUpdates(): Promise<void> {
	return autoUpdater.checkForUpdates().then(() => undefined);
}

export function downloadUpdate(): Promise<void> {
	return autoUpdater.downloadUpdate().then(() => undefined);
}

export function quitAndInstall(): void {
	autoUpdater.quitAndInstall();
}
