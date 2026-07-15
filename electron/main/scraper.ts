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
	final_url?: string;
	error?: string;
	// Base64-encoded PNG bytes for interactive embeds captured via CDP, keyed by the
	// id embedded in the placeholder <img src="https://analecta-shot.invalid/shot/{id}.png">
	// spliced into the DOM in place of each captured element (see captureEmbedShots).
	shots?: Record<string, string>;
}

let renderServer: http.Server | null = null;

// A CDP command can hang indefinitely (never resolve, never reject) rather
// than error out — a plain try/catch does not protect against that. Races
// the given promise against a timer so a single stuck command degrades to a
// rejection instead of hanging the whole render indefinitely.
function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
	return Promise.race([
		promise,
		new Promise<T>((_resolve, reject) => {
			setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
		}),
	]);
}

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

// Registry of rendered-DOM selectors for known interactive-embed types whose
// content is invisible to Tier 1 (JS-populated iframes/custom elements). Each
// entry is captured as a screenshot and spliced back in as a static <img> —
// see captureEmbedShots. Currently: MDN's live-code-sample runner. Twitter/X
// embeds are a deferred, unproven second registry entry (feasibility of
// widgets.js in a headless, logged-out window hasn't been spiked yet).
const EMBED_SELECTORS = ['mdn-live-sample-result'];

// RFC 2606 reserved TLD — guaranteed non-resolvable, can't collide with a
// real image URL on the page. Defuddle strips non-http(s) <img src> values
// entirely (confirmed empirically), so the placeholder must be a
// syntactically normal https:// URL rather than a custom scheme or data: URI.
const SHOT_HOST = 'analecta-shot.invalid';

interface EmbedTarget {
	id: string;
	x: number;
	y: number;
	width: number;
	height: number;
}

/**
 * Screenshot every element matching EMBED_SELECTORS via CDP, then replace each
 * one in the live DOM with a placeholder <img> pointing at a reserved,
 * non-resolvable host — the id in the URL lets the sidecar's AssetDownloader
 * resolve it back to the captured bytes returned in the `shots` map. Must run
 * before Defuddle/outerHTML are read: MDN's client JS tears down the raw
 * placeholder iframe and replaces it with these custom elements shortly after
 * load, and Defuddle drops any element with no recognizable image src.
 *
 * Never lets a failure here escape to the caller — Tier 2 is an existing,
 * shipped path for every low-confidence page, not just ones with a known
 * embed, so a bug in this capture logic must degrade to "no embeds found"
 * rather than sinking the whole render.
 */
async function captureEmbedShots(
	win: BrowserWindow,
	dbg: Electron.Debugger
): Promise<Record<string, string>> {
	const shots: Record<string, string> = {};

	try {
		const selectorList = JSON.stringify(EMBED_SELECTORS.join(','));

		// Give the runtime a moment to finish swapping the placeholder for the
		// real, populated element before measuring anything — must happen
		// before rects are read below, or the clip uses stale/pre-mount
		// geometry (these elements can still be growing/settling at this point).
		await new Promise((resolve) => setTimeout(resolve, 1000));

		// Stamp + measure in the same pass so the id assigned always matches
		// the exact element a rect was taken from — a purely positional
		// (nth-match) id would break if the runtime rebuilds these elements
		// between this call and the splice call below, which is exactly what
		// was observed happening to MDN's raw placeholder iframe earlier in
		// the page lifecycle.
		const targetsJson = (await win.webContents.executeJavaScript(`
			JSON.stringify(Array.from(document.querySelectorAll(${selectorList})).map((el, i) => {
				const id = 'shot-' + i;
				el.setAttribute('data-analecta-embed-id', id);
				const r = el.getBoundingClientRect();
				return { id, x: r.left + window.scrollX, y: r.top + window.scrollY, width: r.width, height: r.height };
			}))
		`)) as string;
		const targets = JSON.parse(targetsJson) as EmbedTarget[];
		console.error(`[scraper] captureEmbedShots: ${targets.length} target(s) found`);
		if (targets.length === 0) return shots;

		for (const t of targets) {
			if (t.width <= 0 || t.height <= 0) continue;
			console.error(`[scraper] capture ${t.id} start (${t.width}x${t.height} at ${t.x},${t.y})`);
			try {
				const result = (await withTimeout(
					dbg.sendCommand('Page.captureScreenshot', {
						format: 'png',
						clip: { x: t.x, y: t.y, width: t.width, height: t.height, scale: 1 },
						captureBeyondViewport: true,
					}),
					8_000,
					`capture ${t.id}`
				)) as { data: string };
				shots[t.id] = result.data;
				console.error(`[scraper] capture ${t.id} done`);
			} catch (err) {
				// Leave this one uncaptured — its element stays in the DOM as-is
				// and the surrounding extraction still succeeds without it. Also
				// catches a timed-out capture (see withTimeout) — a stuck CDP
				// command never rejects on its own, so without this race one
				// bad capture would hang the entire render, not just itself.
				console.error(`[scraper] capture ${t.id} failed: ${String(err)}`);
			}
		}

		if (Object.keys(shots).length === 0) return shots;

		const capturedIds = JSON.stringify(Object.keys(shots));
		// Dimensions (for the width/height attrs below) and a per-capture index
		// (for the alt text below) come from the same targets array used for
		// capture, keyed by id — both are load-bearing against Defuddle's own
		// post-processing, not cosmetic:
		//  - Defuddle's findSmallImages/removeSmallImages heuristic measures
		//    each <img>'s width/height (attribute, inline style, or, failing
		//    those, computed/rendered size) and deletes anything under 33px on
		//    either axis as a likely tracking pixel. A placeholder pointing at
		//    a deliberately non-resolvable host never loads, so its rendered
		//    size collapses well under that threshold unless the real captured
		//    dimensions are supplied explicitly via width/height attributes.
		//  - Defuddle's _deduplicateImages pass treats any run of <img>s that
		//    share identical, non-empty alt text as responsive-image
		//    alternates (e.g. <img>+<noscript><img> pairs) and collapses them
		//    down to one, discarding the rest. A shared literal alt string
		//    across multiple captures on the same page (e.g. several MDN
		//    live-samples) triggers this and silently drops every capture but
		//    the first — confirmed empirically. Suffixing a per-capture index
		//    keeps the text meaningful while making it distinct.
		const dims = JSON.stringify(
			Object.fromEntries(
				targets.map((t) => [t.id, { w: Math.round(t.width), h: Math.round(t.height) }])
			)
		);
		await win.webContents.executeJavaScript(`
			(() => {
				const captured = new Set(${capturedIds});
				const dims = ${dims};
				Array.from(document.querySelectorAll('[data-analecta-embed-id]')).forEach((el, i) => {
					const id = el.getAttribute('data-analecta-embed-id');
					if (!captured.has(id) || !el.parentElement) return;
					const img = document.createElement('img');
					img.src = 'https://${SHOT_HOST}/shot/' + id + '.png';
					img.alt = 'Interactive embed ' + (i + 1);
					img.setAttribute('width', String(dims[id].w));
					img.setAttribute('height', String(dims[id].h));
					el.parentElement.replaceChild(img, el);
				});
			})()
		`);
		console.error('[scraper] captureEmbedShots: splice done');

		return shots;
	} catch (err) {
		console.error(`[scraper] captureEmbedShots failed: ${String(err)}`);
		return {};
	}
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
		// Navigate via loadURL, not the CDP Page.navigate + Page.lifecycleEvent
		// "networkIdle" wait this used previously — confirmed hanging
		// indefinitely on pages with recurring background network activity
		// (e.g. MDN's Glean telemetry beacons), which never let networkIdle
		// fire at all. loadURL()'s own promise already resolves/rejects on
		// the page's load event, sidestepping that wait entirely.
		await win.loadURL(url);
		console.error(`[scraper] loadURL done: ${url}`);

		const dbg = win.webContents.debugger;
		dbg.attach('1.3');
		await dbg.sendCommand('Page.enable');

		const shots = await captureEmbedShots(win, dbg);
		console.error(`[scraper] captureEmbedShots done: ${Object.keys(shots).length} shot(s)`);

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
      final_url: document.baseURI,
    });
  } catch (e) {
    return JSON.stringify({
      ok: false,
      outer_html: document.documentElement.outerHTML,
      final_url: document.baseURI,
      error: String(e),
    });
  }
})()`;

		const resultJson = (await win.webContents.executeJavaScript(script)) as string;
		console.error('[scraper] defuddle script done');
		const result = JSON.parse(resultJson) as RenderResult;
		if (Object.keys(shots).length > 0) result.shots = shots;
		return result;
	} catch (err) {
		console.error(
			`[scraper] scrapeUrl failed: ${err instanceof Error ? err.message : String(err)}`
		);
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
