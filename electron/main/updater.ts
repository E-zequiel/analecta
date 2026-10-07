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

export type UpdateCheckResult =
	| { status: 'available'; version: string }
	| { status: 'up-to-date' }
	| { status: 'unavailable' }
	| { status: 'error' };

// electron-updater semantics (verified against its out/*.d.ts + AppUpdater.js):
// resolves null only when the updater is inactive (unpacked/dev); when the
// latest version is not newer it both emits 'update-not-available' and
// resolves with isUpdateAvailable: false; errors reject the promise and also
// fire the 'error' event already logged by initUpdater.
export async function runUpdateCheck(): Promise<UpdateCheckResult> {
	try {
		const result = await autoUpdater.checkForUpdates();
		if (result !== null && result.isUpdateAvailable) {
			return { status: 'available', version: result.updateInfo.version };
		}
		return { status: 'up-to-date' };
	} catch {
		return { status: 'error' };
	}
}

export function downloadUpdate(): Promise<void> {
	return autoUpdater.downloadUpdate().then(() => undefined);
}

export function quitAndInstall(): void {
	autoUpdater.quitAndInstall();
}
