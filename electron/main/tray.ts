import { app, Menu, Tray, nativeImage, nativeTheme } from 'electron';
import path from 'path';
import fs from 'fs';
import type { BrowserWindow } from 'electron';

let tray: Tray | null = null;
let themeListener: (() => void) | null = null;

function resolveIcon(variant: 'dark' | 'light'): Electron.NativeImage {
	const filename = variant === 'dark' ? 'tray-icon-dark.png' : 'tray-icon-light.png';
	const basePath = app.isPackaged
		? process.resourcesPath
		: path.join(__dirname, '..', '..', 'build-resources');
	const iconPath = path.join(basePath, filename);
	return fs.existsSync(iconPath) ? nativeImage.createFromPath(iconPath) : nativeImage.createEmpty();
}

export function createTray(win: BrowserWindow): void {
	tray = new Tray(resolveIcon(nativeTheme.shouldUseDarkColors ? 'dark' : 'light'));

	themeListener = () => {
		tray?.setImage(resolveIcon(nativeTheme.shouldUseDarkColors ? 'dark' : 'light'));
	};
	nativeTheme.on('updated', themeListener);
	tray.setToolTip('Analecta');

	const buildMenu = () =>
		Menu.buildFromTemplate([
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
	if (themeListener) {
		nativeTheme.off('updated', themeListener);
		themeListener = null;
	}
	tray?.destroy();
	tray = null;
}
