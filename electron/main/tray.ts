import { app, Menu, Tray, nativeImage, Notification } from 'electron';
import path from 'path';
import fs from 'fs';
import type { BrowserWindow } from 'electron';

let tray: Tray | null = null;

export function createTray(win: BrowserWindow): void {
	const iconPath = path.join(__dirname, '..', '..', 'build-resources', 'tray-icon.png');
	// Fall back to an empty image in dev if the icon file is not yet present.
	const icon = fs.existsSync(iconPath)
		? nativeImage.createFromPath(iconPath)
		: nativeImage.createEmpty();

	tray = new Tray(icon);
	tray.setToolTip('Analecta');

	const buildMenu = () =>
		Menu.buildFromTemplate([
			{
				label: 'Add URL from clipboard',
				click: () => {
					if (!Notification.isSupported()) {
						win.show();
						win.focus();
						win.webContents.send('tray-paste-url');
						return;
					}
					const notif = new Notification({
						title: 'Analecta',
						body: 'Add URL from clipboard',
						silent: true,
						timeoutType: 'never',
					});
					notif.once('click', () => {
						win.show();
						win.focus();
						win.webContents.send('tray-paste-url');
					});
					notif.show();
				},
			},
			{
				label: 'Open Analecta',
				click: () => {
					win.show();
					win.focus();
				},
			},
			{
				label: 'Start with system',
				type: 'checkbox' as const,
				checked: app.getLoginItemSettings().openAtLogin,
				click: (item) => app.setLoginItemSettings({ openAtLogin: item.checked }),
			},
			{ type: 'separator' as const },
			{ label: 'Quit', click: () => app.quit() },
		]);

	tray.setContextMenu(buildMenu());
	tray.on('double-click', () => {
		win.show();
		win.focus();
	});
}

export function destroyTray(): void {
	tray?.destroy();
	tray = null;
}
