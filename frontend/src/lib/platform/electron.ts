declare global {
	interface Window {
		electronAPI: {
			invoke(channel: string, ...args: unknown[]): Promise<unknown>;
			on(channel: string, callback: (...args: unknown[]) => void): () => void;
		};
	}
}

export type OpenDialogOptions = {
	title?: string;
	defaultPath?: string;
	filters?: Array<{ name: string; extensions: string[] }>;
	properties?: Array<'openFile' | 'openDirectory' | 'multiSelections'>;
};

function invoke(channel: string, ...args: unknown[]): Promise<unknown> {
	return window.electronAPI.invoke(channel, ...args);
}

export async function getSidecarPort(): Promise<number> {
	return invoke('get-sidecar-port') as Promise<number>;
}

export async function updateVaultScope(path: string): Promise<void> {
	await invoke('update-vault-scope', path);
}

export async function readTextFile(path: string): Promise<string> {
	return invoke('read-file', path) as Promise<string>;
}

export async function writeTextFile(path: string, data: string): Promise<void> {
	await invoke('write-file', { path, data });
}

export async function fileExists(path: string): Promise<boolean> {
	return invoke('file-exists', path) as Promise<boolean>;
}

export async function openDialog(opts: OpenDialogOptions): Promise<string | null> {
	return invoke('open-dialog', opts) as Promise<string | null>;
}

export async function confirm(message: string, title = 'Confirm'): Promise<boolean> {
	const response = (await invoke('show-message-box', {
		type: 'question',
		buttons: ['Cancel', 'OK'],
		defaultId: 1,
		cancelId: 0,
		title,
		message,
	})) as number;
	return response === 1;
}

export async function openUrl(url: string): Promise<void> {
	await invoke('open-url', url);
}

export async function revealInDir(path: string): Promise<void> {
	await invoke('reveal-in-dir', path);
}

export async function clipboardReadText(): Promise<string> {
	return invoke('clipboard-read') as Promise<string>;
}

export async function notify(title: string, body: string): Promise<void> {
	await invoke('notify', { title, body });
}

export async function relaunch(): Promise<void> {
	await invoke('relaunch');
}

export async function checkUpdate(): Promise<void> {
	await invoke('check-update');
}

export async function downloadAndInstallUpdate(): Promise<void> {
	await invoke('download-and-install-update');
}

export async function getLoginItem(): Promise<boolean> {
	return invoke('get-login-item') as Promise<boolean>;
}

export async function setLoginItem(value: boolean): Promise<void> {
	await invoke('set-login-item', value);
}

export async function setCloseToTray(value: boolean): Promise<void> {
	await invoke('set-close-to-tray', value);
}

export function convertFileSrc(path: string): string {
	return `analecta-file://${path}`;
}

export async function getInitialDeepLink(): Promise<string | null> {
	return invoke('get-initial-deep-link') as Promise<string | null>;
}

export function onSidecarReady(cb: (port: number) => void): () => void {
	return window.electronAPI.on('sidecar-ready', (...args: unknown[]) => cb(args[0] as number));
}

export function onDeepLink(cb: (url: string) => void): () => void {
	return window.electronAPI.on('deep-link', (...args: unknown[]) => cb(args[0] as string));
}

export function onUpdateAvailable(cb: (info: unknown) => void): () => void {
	return window.electronAPI.on('update-available', (...args: unknown[]) => cb(args[0]));
}

export async function windowMinimize(): Promise<void> {
	await invoke('window-minimize');
}

export async function windowMaximize(): Promise<void> {
	await invoke('window-maximize');
}

export async function windowClose(): Promise<void> {
	await invoke('window-close');
}

export async function windowStartMove(): Promise<void> {
	await invoke('window-start-move');
}

export async function windowStartResize(edge: string): Promise<void> {
	await invoke('window-start-resize', edge);
}

export async function windowIsMaximized(): Promise<boolean> {
	return invoke('window-is-maximized') as Promise<boolean>;
}

export function onWindowMaximized(cb: (maximized: boolean) => void): () => void {
	return window.electronAPI.on('window-maximized', (...args: unknown[]) => cb(args[0] as boolean));
}

export function onTrayPasteUrl(cb: () => void): () => void {
	return window.electronAPI.on('tray-paste-url', () => cb());
}
