/**
 * Single source of truth for the Chrome identity Analecta presents to the
 * open web — a generic current Chrome-on-Linux, never an Electron- or
 * Analecta-identifying string. See docs/privacy.md.
 *
 * process.versions.chrome is Electron's own bundled Chromium build, so this
 * major version tracks reality automatically on every Electron bump —
 * nothing here goes stale on its own. Exported so sidecar.ts can pass the
 * same major version to the Python sidecar (ANALECTA_CHROME_MAJOR),
 * keeping Tier 1 headers and the Tier 2 render window from ever drifting
 * apart.
 */

export const CHROME_MAJOR = process.versions.chrome.split('.')[0];

/**
 * Mirrors Chrome's own reduced/frozen UA format (minor/build/patch zeroed)
 * rather than Electron's real, specific Chromium build — see
 * https://www.chromium.org/updates/ua-reduction/. A precise build number
 * would itself be a sharper fingerprint than what real Chrome now sends.
 */
export function buildChromeUserAgent(): string {
	return `Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${CHROME_MAJOR}.0.0.0 Safari/537.36`;
}
