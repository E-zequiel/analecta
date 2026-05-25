import { app, BrowserWindow, Menu } from 'electron';
import path from 'path';
import { registerProtocols, setupProtocolHandlers } from './protocols.js';
import { spawnSidecar, killSidecar, getSidecarPort } from './sidecar.js';
import { startRenderServer, stopRenderServer } from './scraper.js';
import { registerIpcHandlers, setInitialDeepLink, setMainWindowRef } from './ipc.js';
import { createTray, destroyTray } from './tray.js';
import { initUpdater } from './updater.js';

/** True when running under Wayland. focus() is best-effort; always call show() first. */
export const isWaylandNative = process.env.XDG_SESSION_TYPE === 'wayland';

// Must be set before app.ready: display name and XDG-compliant userData path.
app.setName('Analecta');
app.setPath('userData', path.join(app.getPath('home'), '.config', 'analecta'));

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
    minWidth: 700,
    minHeight: 500,
    frame: false,
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

// On Linux, xdg-open passes the deep-link URL as a CLI argument to the first instance.
// macOS uses the open-url event instead; second-instance covers subsequent launches on both.
const deepLinkArg = process.argv.find(arg => arg.startsWith('analecta://'));
if (deepLinkArg) setInitialDeepLink(deepLinkArg);

app.whenReady().then(async () => {
  Menu.setApplicationMenu(null);
  setupProtocolHandlers();

  mainWindow = createWindow();
  setMainWindowRef(mainWindow);
  registerIpcHandlers();

  mainWindow.on('maximize',   () => mainWindow!.webContents.send('window-maximized', true));
  mainWindow.on('unmaximize', () => mainWindow!.webContents.send('window-maximized', false));

  const { port: renderPort, token: renderToken } = await startRenderServer();
  spawnSidecar(renderPort, renderToken);

  initUpdater(mainWindow);

  mainWindow.webContents.on('did-fail-load', (_e, code, desc, url) => {
    console.error(`[main] did-fail-load: ${url} — ${code} ${desc}`);
    mainWindow?.webContents.openDevTools();
  });

  const devUrl = process.env.VITE_DEV_SERVER_URL;
  if (devUrl) {
    mainWindow.loadURL(devUrl);
    mainWindow.webContents.openDevTools();
    mainWindow.webContents.on('before-input-event', (_e, input) => {
      if (input.type === 'keyDown' && input.key === 'F12') {
        mainWindow?.webContents.toggleDevTools();
      }
    });
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
  stopRenderServer();
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
