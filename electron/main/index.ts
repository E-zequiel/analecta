import { app, BrowserWindow, Menu } from 'electron';
import path from 'path';
import { writeFileSync, mkdirSync } from 'fs';
import { spawnSync } from 'child_process';
import { registerProtocols, setupProtocolHandlers } from './protocols.js';
import { spawnSidecar, killSidecar } from './sidecar.js';
import { startRenderServer, stopRenderServer } from './scraper.js';
import {
	registerIpcHandlers,
	setInitialDeepLink,
	setMainWindowRef,
	getCloseToTray,
} from './ipc.js';
import { createTray, destroyTray } from './tray.js';
import { initUpdater } from './updater.js';

/** True when running under Wayland. focus() is best-effort; always call show() first. */
export const isWaylandNative = process.env.XDG_SESSION_TYPE === 'wayland';

/**
 * Writes a .desktop file for the dev build and associates it with the
 * analecta:// scheme via xdg-mime.  Only runs when !app.isPackaged — the
 * packaged .deb/.rpm/.AppImage ships a proper .desktop entry that the system
 * installer registers automatically.
 *
 * Overrides any stale handler left over from earlier dev builds or prior
 * packagers (e.g. the Tauri analecta-handler.desktop).
 */
function registerDevSchemeHandler(): void {
	const desktopDir = path.join(app.getPath('home'), '.local', 'share', 'applications');
	const desktopFile = path.join(desktopDir, 'analecta-dev.desktop');
	const entry = [
		'[Desktop Entry]',
		'Type=Application',
		'Name=Analecta (dev)',
		`Exec=${process.execPath} ${app.getAppPath()} %u`,
		'MimeType=x-scheme-handler/analecta',
		'NoDisplay=true',
		'StartupNotify=false',
		'',
	].join('\n');
	try {
		mkdirSync(desktopDir, { recursive: true });
		writeFileSync(desktopFile, entry, 'utf-8');
		spawnSync('xdg-mime', ['default', 'analecta-dev.desktop', 'x-scheme-handler/analecta'], {
			stdio: 'ignore',
		});
		spawnSync('update-desktop-database', [desktopDir], { stdio: 'ignore' });
	} catch (err) {
		console.warn('[main] failed to register dev scheme handler:', err);
	}
}

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
	const deepLink = argv.find((arg) => arg.startsWith('analecta://'));
	if (mainWindow) {
		if (deepLink) mainWindow.webContents.send('deep-link', deepLink);
		mainWindow.show();
		mainWindow.focus();
	}
});

app.setAsDefaultProtocolClient('analecta');

// On Linux, xdg-open passes the deep-link URL as a CLI argument to the first instance.
// macOS uses the open-url event instead; second-instance covers subsequent launches on both.
const deepLinkArg = process.argv.find((arg) => arg.startsWith('analecta://'));
if (deepLinkArg) setInitialDeepLink(deepLinkArg);

app
	.whenReady()
	.then(async () => {
		Menu.setApplicationMenu(null);
		setupProtocolHandlers();
		if (!app.isPackaged) registerDevSchemeHandler();

		mainWindow = createWindow();
		setMainWindowRef(mainWindow);
		registerIpcHandlers();

		mainWindow.on('maximize', () => mainWindow!.webContents.send('window-maximized', true));
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
			void mainWindow.loadURL(devUrl);
			mainWindow.webContents.openDevTools();
			mainWindow.webContents.on('before-input-event', (_e, input) => {
				if (input.type === 'keyDown' && input.key === 'F12') {
					mainWindow?.webContents.toggleDevTools();
				}
			});
		} else {
			void mainWindow.loadURL('app://index.html');
		}

		createTray(mainWindow);

		mainWindow.on('close', (event) => {
			if (!isQuitting && getCloseToTray()) {
				event.preventDefault();
				mainWindow?.hide();
			}
		});
	})
	.catch((err: unknown) => {
		console.error('[main] startup error:', err);
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
