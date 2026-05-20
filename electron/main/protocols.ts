import { protocol, session, net, app } from 'electron';
import path from 'path';
import { assertVaultPath } from './vault-state.js';

const ALLOWED_IMAGE_EXTS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.avif']);

const CSP = [
  "default-src 'self' app:",
  "connect-src 'self' http://localhost:* app:",
  "img-src 'self' app: analecta-file: data: blob: https:",
  "style-src 'self' 'unsafe-inline' app:",
  "font-src 'self' app:",
  "script-src 'self' app:",
  "object-src 'none'",
  "base-uri 'self'",
].join('; ');

/** Called before app.ready — registers custom schemes as privileged. */
export function registerProtocols(): void {
  protocol.registerSchemesAsPrivileged([
    { scheme: 'app', privileges: { standard: true, secure: true, supportFetchAPI: true } },
    { scheme: 'analecta-file', privileges: { standard: true, secure: true, supportFetchAPI: true } },
  ]);
}

/** Called after app.ready — registers actual request handlers and CSP injection. */
export function setupProtocolHandlers(): void {
  const frontendBuildPath = app.isPackaged
    ? path.join(process.resourcesPath, 'frontend-build')
    : path.join(__dirname, '..', '..', '..', 'frontend', 'build');

  // app:// — serves the SvelteKit static build
  session.defaultSession.protocol.handle('app', async (request) => {
    const url = new URL(request.url);
    let filePath = path.join(frontendBuildPath, url.pathname);
    if (!path.extname(filePath)) {
      filePath = path.join(frontendBuildPath, 'index.html');
    }
    const resolved = path.resolve(filePath);
    if (!resolved.startsWith(path.resolve(frontendBuildPath))) {
      return new Response('Not found', { status: 404 });
    }
    return net.fetch(`file://${resolved}`);
  });

  // analecta-file:// — serves vault images; enforces vault scope + extension allowlist
  session.defaultSession.protocol.handle('analecta-file', async (request) => {
    const url = new URL(request.url);
    const filePath = decodeURIComponent(url.pathname);
    const ext = path.extname(filePath).toLowerCase();

    if (!ALLOWED_IMAGE_EXTS.has(ext)) {
      return new Response('Forbidden', { status: 403 });
    }
    try {
      assertVaultPath(filePath);
    } catch {
      return new Response('Forbidden', { status: 403 });
    }
    return net.fetch(`file://${filePath}`);
  });

  // Inject CSP into every response
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [CSP],
      },
    });
  });
}
