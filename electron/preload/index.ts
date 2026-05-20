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
  'get-initial-deep-link',
]);

const ALLOWED_PUSH_CHANNELS = new Set(['sidecar-ready', 'deep-link', 'update-available']);

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
