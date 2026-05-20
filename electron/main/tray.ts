import { app, Menu, Tray, clipboard, nativeImage } from 'electron';
import path from 'path';
import fs from 'fs';
import type { BrowserWindow } from 'electron';

let tray: Tray | null = null;

export function createTray(win: BrowserWindow, getSidecarPort: () => Promise<number>): void {
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
        click: async () => {
          const url = clipboard.readText().trim();
          if (!url) return;
          const port = await getSidecarPort();
          fetch(`http://localhost:${port}/api/v1/extract`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url }),
          }).catch(err => console.error('[tray] extract failed:', err));
        },
      },
      {
        label: 'Open Analecta',
        click: () => { win.show(); win.focus(); },
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
  tray.on('double-click', () => { win.show(); win.focus(); });
}

export function destroyTray(): void {
  tray?.destroy();
  tray = null;
}
