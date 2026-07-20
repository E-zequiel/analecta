import { app, type Session } from 'electron';
import path from 'path';
import { readFileSync } from 'fs';
import { ElectronBlocker } from '@ghostery/adblocker-electron';

// extraResources copies this to {resources}/filters/easyprivacy.txt in packaged
// builds, same pattern as defuddleBundlePath in scraper.ts. In dev, __dirname is
// electron/dist/main/, so we go up two levels to electron/filters/.
const easyPrivacyPath = app.isPackaged
	? path.join(process.resourcesPath, 'filters', 'easyprivacy.txt')
	: path.join(__dirname, '..', '..', 'filters', 'easyprivacy.txt');

/**
 * Blocks network requests to known analytics/telemetry/tracking-pixel hosts in
 * the given session, using a locally-vendored EasyPrivacy filter list (see
 * scripts/update_filter_list.py) — no runtime fetch of the list itself, which
 * would itself be a periodic outbound call defeating the point. Deliberately
 * EasyPrivacy only, not an ads list (EasyList): the goal is closing a privacy
 * gap, not ad removal, and a broader list risks blocking page resources that
 * Tier 2's extraction (including MDN live-sample embed screenshots) depends on.
 *
 * `loadCosmeticFilters: false` — Tier 2 is a headless window read by Defuddle,
 * never shown to a person, so element-hiding CSS/JS injection is pointless and
 * risks perturbing the DOM right before it's read. This also means the
 * blocker never registers a preload script or IPC handlers, only the
 * `webRequest.onBeforeRequest`/`onHeadersReceived` network-level hooks.
 *
 * `loadCSPFilters: false` — some EasyPrivacy rules tighten the page's CSP
 * (via a `$csp` modifier) instead of blocking a request outright, to stop
 * inline-script trackers a network block can't catch. Tier 2's entire job is
 * executing the page's own JS so Defuddle sees the hydrated DOM (including
 * elements like `mdn-live-sample-result` that `captureEmbedShots` screenshots)
 * — a filter-injected CSP restricting inline/eval script risks suppressing
 * exactly that hydration. `onBeforeRequest` already blocks the tracker
 * *network* requests; the marginal privacy gain from also rewriting CSP
 * isn't worth that extraction-fidelity risk.
 *
 * Never lets a failure here escape to the caller — same convention as the
 * other Tier 2 helpers in scraper.ts. A bug in the vendored list or the parser
 * must degrade to "no blocking this run," not break extraction entirely.
 */
export function enableTrackerBlocking(scrapingSession: Session): void {
	try {
		const rules = readFileSync(easyPrivacyPath, 'utf-8');
		const blocker = ElectronBlocker.parse(rules, {
			loadCosmeticFilters: false,
			loadCSPFilters: false,
		});
		blocker.enableBlockingInSession(scrapingSession);
	} catch (err) {
		console.error(`[tracker-blocking] setup failed, proceeding unblocked: ${String(err)}`);
	}
}
