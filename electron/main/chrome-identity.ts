/**
 * Single source of truth for the Chrome identity Analecta's Tier 1 fetches
 * present to the open web — a generic current Chrome-on-Linux, never an
 * Electron- or Analecta-identifying string. See docs/privacy.md.
 *
 * process.versions.chrome is Electron's own bundled Chromium build, so this
 * major version tracks reality automatically on every Electron bump —
 * nothing here goes stale on its own. Exported so sidecar.ts can pass the
 * same major version to the Python sidecar (ANALECTA_CHROME_MAJOR), so Tier
 * 1's claimed UA doesn't drift from the Chromium build actually bundled with
 * the app.
 */

export const CHROME_MAJOR = process.versions.chrome.split('.')[0];
