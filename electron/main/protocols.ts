import { protocol, session, net, app } from 'electron';
import path from 'path';
import { readFileSync } from 'fs';
import { createHash } from 'crypto';
import { assertVaultPath } from './vault-state.js';

const ALLOWED_IMAGE_EXTS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.avif']);

/**
 * Computes SHA-256 hashes for every attribute-less inline <script> in index.html.
 * SvelteKit injects one such script per build (base URL init); the name changes each
 * build so we compute the hash at runtime rather than hardcoding it.
 */
function inlineScriptHashes(indexHtmlPath: string): string[] {
	try {
		const html = readFileSync(indexHtmlPath, 'utf8');
		const hashes: string[] = [];
		const re = /<script>([\s\S]*?)<\/script>/g;
		let m: RegExpExecArray | null;
		while ((m = re.exec(html)) !== null) {
			const digest = createHash('sha256').update(m[1]).digest('base64');
			hashes.push(`'sha256-${digest}'`);
		}
		return hashes;
	} catch (err) {
		console.warn('[protocols] could not compute inline script hashes:', err);
		return [];
	}
}

/** Called before app.ready — registers custom schemes as privileged. */
export function registerProtocols(): void {
	protocol.registerSchemesAsPrivileged([
		{ scheme: 'app', privileges: { standard: true, secure: true, supportFetchAPI: true } },
		{
			scheme: 'analecta-file',
			privileges: { standard: true, secure: true, supportFetchAPI: true },
		},
	]);
}

/** Called after app.ready — registers actual request handlers and CSP injection. */
export function setupProtocolHandlers(): void {
	const frontendBuildPath = app.isPackaged
		? path.join(process.resourcesPath, 'frontend-build')
		: path.join(__dirname, '..', '..', '..', 'frontend', 'build');

	const scriptHashes = inlineScriptHashes(path.join(frontendBuildPath, 'index.html'));

	const csp = [
		"default-src 'self' app:",
		"connect-src 'self' http://localhost:* app:",
		"img-src 'self' app: analecta-file: data: blob:",
		"style-src-elem 'self' app:",
		"style-src-attr 'none'",
		"font-src 'self' app:",
		`script-src 'self' ${scriptHashes.join(' ')} app:`,
		"object-src 'none'",
		"base-uri 'self'",
	].join('; ');

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
		try {
			return await net.fetch(`file://${resolved}`);
		} catch (err) {
			console.error(`[protocols] app:// fetch error for ${resolved}:`, err);
			return new Response('Internal error', { status: 500 });
		}
	});

	// analecta-file:// — serves vault images; enforces vault scope + extension allowlist
	session.defaultSession.protocol.handle('analecta-file', async (request) => {
		const url = new URL(request.url);
		// Chromium normalises analecta-file:///mnt/... → analecta-file://mnt/...
		// (first path component becomes the hostname) for standard schemes.
		// Reconstruct the absolute path by prepending the hostname when it is not
		// empty and not the conventional 'localhost' placeholder.
		const host = url.hostname;
		const pathPart = decodeURIComponent(url.pathname);
		const filePath = host && host !== 'localhost' ? `/${host}${pathPart}` : pathPart;
		const ext = path.extname(filePath).toLowerCase();

		if (!ALLOWED_IMAGE_EXTS.has(ext)) {
			return new Response('Forbidden', { status: 403 });
		}
		try {
			assertVaultPath(filePath);
		} catch {
			return new Response('Forbidden', { status: 403 });
		}
		try {
			return await net.fetch(`file://${filePath}`);
		} catch {
			// Missing vault image is routine user-caused state (deleted asset,
			// moved vault, hand-edited markdown) — not worth logging per miss.
			return new Response('Not found', { status: 404 });
		}
	});

	// Inject CSP only in packaged builds; dev uses Vite's own headers.
	if (app.isPackaged) {
		session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
			callback({
				responseHeaders: {
					...details.responseHeaders,
					'Content-Security-Policy': [csp],
				},
			});
		});
	}
}
