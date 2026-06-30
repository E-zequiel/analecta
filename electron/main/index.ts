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
	const iconPath = path.join(__dirname, '..', '..', 'build-resources', 'icons', '512x512.png');
	const entry = [
		'[Desktop Entry]',
		'Type=Application',
		'Name=Analecta (dev)',
		`Exec=${process.execPath} ${app.getAppPath()} %u`,
		`Icon=${iconPath}`,
		'MimeType=x-scheme-handler/analecta',
		'StartupWMClass=Analecta',
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

// On Wayland the compositor resolves the taskbar/alt-tab icon by matching the
// xdg_toplevel app-id against a .desktop filename. In dev the file is
// analecta-dev.desktop; production uses the binary name (analecta) which
// electron-builder writes automatically.
if (!app.isPackaged && process.platform === 'linux') {
	app.setDesktopFileName('analecta-dev');
	// Write the .desktop entry here (pre-ready) so the icon is available to the
	// compositor before the first window opens. app.getPath/getAppPath are safe
	// to call before ready.
	registerDevSchemeHandler();
}

// Register custom schemes before app.ready (Electron requirement).
registerProtocols();

// Single-instance lock: quit immediately if another instance is already running.
if (!app.requestSingleInstanceLock()) {
	app.quit();
	process.exit(0);
}

let mainWindow: BrowserWindow | null = null;
let isQuitting = false;

function resolveAppIcon(): string {
	return app.isPackaged
		? path.join(process.resourcesPath, 'icon.png')
		: path.join(__dirname, '..', '..', 'build-resources', 'icons', '512x512.png');
}

function createWindow(): BrowserWindow {
	return new BrowserWindow({
		width: 1280,
		height: 800,
		minWidth: 700,
		minHeight: 500,
		frame: false,
		icon: resolveAppIcon(),
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

		mainWindow = createWindow();
		setMainWindowRef(mainWindow);
		registerIpcHandlers();

		mainWindow.on('maximize', () => mainWindow!.webContents.send('window-maximized', true));
		mainWindow.on('unmaximize', () => mainWindow!.webContents.send('window-maximized', false));

		// Wayland tiling WM workaround — confirmed on COSMIC (Pop!_OS), likely affects
		// Sway, Hyprland, and KDE Plasma tiling as well.
		//
		// When a WM-initiated unmaximize (e.g. Super+M in COSMIC) restores a window
		// that was moved to a different tile between maximize and unmaximize, the
		// compositor may configure the window at stale bounds instead of the correct
		// tile size. Electron's getBounds() reflects the wrong size and the committed
		// wl_buffer is never updated to match the tile, leaving a visual gap on the
		// right and bottom edges.
		//
		// The application-initiated path (win.unmaximize()) does not trigger this
		// because Electron drives the full xdg_toplevel configure cycle itself.
		//
		// Fix: track the last normal bounds; after unmaximize settle, if they diverge
		// from the current bounds, call setBounds() with the saved value. This sends a
		// real size change to the compositor, which processes it and closes the gap.
		// On well-behaved compositors (GNOME/Mutter) the sizes match and no setBounds
		// is ever called — the guard makes this path a safe no-op.
		if (isWaylandNative) {
			let _wmMaximized = false;
			let _pendingUnmaximize = false;
			let _lastNormalBounds: { width: number; height: number } | null = null;
			let _resizeSettleTimer: ReturnType<typeof setTimeout> | null = null;
			let _normalBoundsTimer: ReturnType<typeof setTimeout> | null = null;

			const captureNormalBounds = () => {
				if (_normalBoundsTimer) clearTimeout(_normalBoundsTimer);
				_normalBoundsTimer = setTimeout(() => {
					if (!_wmMaximized && !_pendingUnmaximize) {
						const b = mainWindow!.getBounds();
						_lastNormalBounds = { width: b.width, height: b.height };
					}
				}, 50);
			};

			mainWindow.on('maximize', () => {
				_wmMaximized = true;
			});
			mainWindow.on('unmaximize', () => {
				_wmMaximized = false;
				_pendingUnmaximize = true;
			});

			mainWindow.on('resize', () => {
				captureNormalBounds();
				if (!_pendingUnmaximize) return;
				if (_resizeSettleTimer) clearTimeout(_resizeSettleTimer);
				_resizeSettleTimer = setTimeout(() => {
					_pendingUnmaximize = false;
					if (!mainWindow || mainWindow.isDestroyed() || !_lastNormalBounds) return;
					if (mainWindow.isMaximized()) return;
					const cur = mainWindow.getBounds();
					if (cur.width !== _lastNormalBounds.width || cur.height !== _lastNormalBounds.height) {
						mainWindow.setSize(_lastNormalBounds.width, _lastNormalBounds.height);
					}
				}, 150);
			});

			mainWindow.on('move', () => {
				captureNormalBounds();
			});
		}

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
