import { contextBridge, ipcRenderer } from 'electron';

const ALLOWED_CHANNELS = new Set([
	'get-sidecar-port',
	'update-vault-scope',
	'read-file',
	'write-file',
	'file-exists',
	'open-dialog',
	'show-message-box',
	'open-url',
	'reveal-in-dir',
	'clipboard-read',
	'notify',
	'check-update',
	'download-and-install-update',
	'relaunch',
	'get-login-item',
	'set-login-item',
	'set-close-to-tray',
	'get-initial-deep-link',
	'window-minimize',
	'window-maximize',
	'window-close',
	'window-start-move',
	'window-start-resize',
	'window-is-maximized',
]);

const ALLOWED_PUSH_CHANNELS = new Set([
	'sidecar-ready',
	'deep-link',
	'update-available',
	'window-maximized',
	'tray-paste-url',
]);

const api = {
	invoke(channel: string, ...args: unknown[]): Promise<unknown> {
		if (!ALLOWED_CHANNELS.has(channel)) {
			return Promise.reject(new Error(`blocked channel: ${channel}`));
		}
		return ipcRenderer.invoke(channel, ...args);
	},

	on(channel: string, callback: (...args: unknown[]) => void): () => void {
		if (!ALLOWED_PUSH_CHANNELS.has(channel)) {
			throw new Error(`blocked channel: ${channel}`);
		}
		const listener = (_event: Electron.IpcRendererEvent, ...args: unknown[]) => callback(...args);
		ipcRenderer.on(channel, listener);
		return () => ipcRenderer.removeListener(channel, listener);
	},
};

contextBridge.exposeInMainWorld('electronAPI', api);

declare global {
	interface Window {
		electronAPI: typeof api;
	}
}
