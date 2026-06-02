import { app, BrowserWindow, session } from 'electron';
import http from 'node:http';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import net from 'node:net';
import crypto from 'node:crypto';

export interface RenderResult {
	ok: boolean;
	content?: string;
	title?: string;
	author?: string;
	description?: string;
	published?: string;
	outer_html?: string;
	error?: string;
}

let renderServer: http.Server | null = null;

// extraResources copies the bundle to {resources}/defuddle-browser.js in packaged builds.
// In dev, __dirname is electron/dist/main/ so we go up two levels to electron/node_modules/.
const defuddleBundlePath = app.isPackaged
	? path.join(process.resourcesPath, 'defuddle-browser.js')
	: path.join(__dirname, '..', '..', 'node_modules', 'defuddle', 'dist', 'index.full.js');

const defuddleBundle = readFileSync(defuddleBundlePath, 'utf-8');

function findFreePort(): Promise<number> {
	return new Promise((resolve, reject) => {
		const srv = net.createServer();
		srv.listen(0, '127.0.0.1', () => {
			const addr = srv.address() as net.AddressInfo;
			srv.close(() => resolve(addr.port));
		});
		srv.on('error', reject);
	});
}

function validateScrapeUrl(urlStr: string): void {
	let parsed: URL;
	try {
		parsed = new URL(urlStr);
	} catch {
		throw new Error('invalid-url');
	}
	if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('blocked-protocol');
	const h = parsed.hostname.toLowerCase().replace(/^\[|]$/g, '');
	// Block loopback: 127.0.0.0/8, ::1, and IPv4-mapped IPv6 (::ffff:127.x.x.x).
	if (h === 'localhost' || h === '::1') throw new Error('blocked-local');
	if (/^127\./.test(h)) throw new Error('blocked-local');
	if (/^::ffff:(127\.|7f)/i.test(h)) throw new Error('blocked-local');
	// Block link-local: 169.254.0.0/16 and ::ffff:169.254.x.x.
	if (/^169\.254\./.test(h)) throw new Error('blocked-link-local');
	if (/^::ffff:(169\.254\.|a9fe)/i.test(h)) throw new Error('blocked-link-local');
	// Block RFC 1918.
	if (/^10\.|^172\.(1[6-9]|2\d|3[01])\.|^192\.168\./.test(h)) throw new Error('blocked-rfc1918');
}

async function scrapeUrl(url: string): Promise<RenderResult> {
	const scrapingSession = session.fromPartition('persist:scraping', { cache: false });

	const win = new BrowserWindow({
		show: false,
		webPreferences: {
			sandbox: true,
			contextIsolation: true,
			nodeIntegration: false,
			nodeIntegrationInWorker: false,
			webSecurity: true,
			session: scrapingSession,
			// No preload — scraping window has no IPC surface.
		},
	});

	try {
		const dbg = win.webContents.debugger;
		dbg.attach('1.3');
		await dbg.sendCommand('Page.enable');
		await dbg.sendCommand('Network.enable');
		await dbg.sendCommand('Page.setLifecycleEventsEnabled', { enabled: true });

		await new Promise<void>((resolve, reject) => {
			const timeout = setTimeout(() => resolve(), 30_000);

			const onMessage = (_e: Electron.Event, method: string, params: Record<string, unknown>) => {
				if (method === 'Page.lifecycleEvent' && params['name'] === 'networkIdle') {
					clearTimeout(timeout);
					dbg.removeListener('message', onMessage);
					resolve();
				}
			};
			dbg.on('message', onMessage);

			win.webContents.once('did-fail-load', (_e, _code, desc) => {
				clearTimeout(timeout);
				dbg.removeListener('message', onMessage);
				reject(new Error(`load-failed: ${desc}`));
			});

			dbg.sendCommand('Page.navigate', { url }).catch((err: unknown) => {
				clearTimeout(timeout);
				dbg.removeListener('message', onMessage);
				reject(err instanceof Error ? err : new Error(String(err)));
			});
		});

		const script =
			defuddleBundle +
			`\n;(async () => {
  try {
    const r = await new Defuddle(document, { url: ${JSON.stringify(url)} }).parseAsync();
    return JSON.stringify({
      ok: true,
      content: r.content,
      title: r.title,
      author: r.author,
      description: r.description,
      published: r.published,
    });
  } catch (e) {
    return JSON.stringify({
      ok: false,
      outer_html: document.documentElement.outerHTML,
      error: String(e),
    });
  }
})()`;

		const resultJson = (await win.webContents.executeJavaScript(script)) as string;
		return JSON.parse(resultJson) as RenderResult;
	} catch (err) {
		return {
			ok: false,
			outer_html: '',
			error: err instanceof Error ? err.message : String(err),
		};
	} finally {
		if (!win.isDestroyed()) win.destroy();
	}
}

function readBody(req: http.IncomingMessage): Promise<string> {
	return new Promise((resolve, reject) => {
		let body = '';
		req.on('data', (chunk: Buffer) => {
			body += chunk.toString('utf-8');
		});
		req.on('end', () => resolve(body));
		req.on('error', reject);
	});
}

export async function startRenderServer(): Promise<{ port: number; token: string }> {
	const port = await findFreePort();
	const token = crypto.randomUUID();

	// eslint-disable-next-line @typescript-eslint/no-misused-promises
	renderServer = http.createServer(async (req, res) => {
		if (req.headers['x-render-token'] !== token) {
			res
				.writeHead(401, { 'Content-Type': 'application/json' })
				.end(JSON.stringify({ error: 'unauthorized' }));
			return;
		}

		if (req.method !== 'POST' || req.url !== '/render') {
			res
				.writeHead(404, { 'Content-Type': 'application/json' })
				.end(JSON.stringify({ error: 'not-found' }));
			return;
		}

		let body: string;
		try {
			body = await readBody(req);
		} catch {
			res
				.writeHead(400, { 'Content-Type': 'application/json' })
				.end(JSON.stringify({ error: 'bad-request' }));
			return;
		}

		let url: string;
		try {
			({ url } = JSON.parse(body) as { url: string });
			validateScrapeUrl(url);
		} catch (err) {
			const msg = err instanceof Error ? err.message : 'bad-request';
			res
				.writeHead(400, { 'Content-Type': 'application/json' })
				.end(JSON.stringify({ error: msg }));
			return;
		}

		try {
			const result = await scrapeUrl(url);
			res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(result));
		} catch (err) {
			const msg = err instanceof Error ? err.message : 'scrape-error';
			res
				.writeHead(500, { 'Content-Type': 'application/json' })
				.end(JSON.stringify({ error: msg }));
		}
	});

	await new Promise<void>((resolve, reject) => {
		renderServer!.listen(port, '127.0.0.1', resolve);
		renderServer!.on('error', reject);
	});

	return { port, token };
}

export function stopRenderServer(): void {
	renderServer?.close();
	renderServer = null;
}
