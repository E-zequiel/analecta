import { app, BrowserWindow } from 'electron';
import path from 'path';
import { registerProtocols, setupProtocolHandlers } from './protocols.js';
import { spawnSidecar, killSidecar, getSidecarPort } from './sidecar.js';
import { registerIpcHandlers, setInitialDeepLink, setMainWindowRef } from './ipc.js';
import { createTray, destroyTray } from './tray.js';
import { initUpdater } from './updater.js';

/** True when running under Wayland. focus() is best-effort; always call show() first. */
export const isWaylandNative = process.env.XDG_SESSION_TYPE === 'wayland';

// Register custom schemes before app.ready (Electron requirement).
registerProtocols();

// Single-instance lock: quit immediately if another instance is already running.
if (!app.requestSingleInstanceLock()) {
  app.quit();
  process.exit(0);
}

let mainWindow: BrowserWindow | null = null;
let isQuitting = false;

function createWindow(): BrowserWindow {
  return new BrowserWindow({
    width: 1280,
    height: 800,
    webPreferences: {
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      nodeIntegrationInWorker: false,
      webSecurity: true,
      allowRunningInsecureContent: false,
      experimentalFeatures: false,
      preload: path.join(__dirname, '..', 'preload', 'index.js'),
    },
  });
}

// When a second instance is launched, focus the existing window and handle deep-link.
app.on('second-instance', (_event, argv) => {
  const deepLink = argv.find(arg => arg.startsWith('analecta://'));
  if (mainWindow) {
    if (deepLink) mainWindow.webContents.send('deep-link', deepLink);
    mainWindow.show();
    mainWindow.focus();
  }
});

app.setAsDefaultProtocolClient('analecta');

app.whenReady().then(() => {
  setupProtocolHandlers();

  mainWindow = createWindow();
  setMainWindowRef(mainWindow);
  registerIpcHandlers();
  spawnSidecar();
  initUpdater(mainWindow);

  const devUrl = process.env.VITE_DEV_SERVER_URL;
  if (devUrl) {
    mainWindow.loadURL(devUrl);
    mainWindow.webContents.openDevTools();
  } else {
    mainWindow.loadURL('app://index.html');
  }

  createTray(mainWindow, getSidecarPort);

  // Closing the window hides to tray; only app.quit() (from tray menu) actually exits.
  mainWindow.on('close', event => {
    if (!isQuitting) {
      event.preventDefault();
      mainWindow?.hide();
    }
  });
});

app.on('before-quit', () => {
  isQuitting = true;
  killSidecar();
  destroyTray();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

// macOS: deep-link delivered here when app is already running.
app.on('open-url', (_event, url) => {
  if (mainWindow) {
    mainWindow.webContents.send('deep-link', url);
  } else {
    setInitialDeepLink(url);
  }
});
