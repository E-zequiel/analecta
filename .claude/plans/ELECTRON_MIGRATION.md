# Electron Shell Migration

## Context

Analecta runs on a three-layer architecture: Electron shell (TypeScript) + Python FastAPI
sidecar + SvelteKit frontend. This document covers the migration of the shell layer from
Tauri 2.x (`src-tauri/`) to Electron 42.x (`electron/`).

The Python sidecar and SvelteKit frontend are **unchanged in logic**. Only the shell layer
is replaced. The full rationale and trade-off analysis are in `docs/electron-migration.md`.
The security model is in `docs/electron-shell-security.md`.

**Branch:** `feat/electron-shell` (off `main`).  
**Status:** E0 done. Starting E1.

---

## Locked Decisions

| Axis | Decision |
|------|----------|
| Shell | Electron 42.x (`electron/`) |
| Frontend | SvelteKit + TypeScript + Vite — unchanged |
| Sidecar | Python FastAPI + PyInstaller — unchanged |
| IPC | contextBridge + `ipcMain.handle()` — replicates Tauri capabilities |
| FS access | `vault-state.ts` (assertVaultPath) — replicates Tauri FS capability |
| Custom protocols | `app://` (frontend), `analecta-file://` (vault images) |
| Packaging | electron-builder — deb, rpm, AppImage |
| Updates | electron-updater |
| Sidecar path (dev) | `src-tauri/binaries/analecta-sidecar/` (until E8) |
| Sidecar path (packaged) | `process.resourcesPath/analecta-sidecar/` |
| Frontend dev URL | `http://localhost:5173` (Vite dev server) |
| Frontend prod URL | `app://index.html` |

---

## Source Layout (target, post-E8)

```
analecta/
├── backend/                          # Python sidecar — unchanged
├── frontend/                         # SvelteKit — logic unchanged, imports updated (E5)
│   └── src/lib/platform/             # NEW (E4): platform shim replacing @tauri-apps/*
│       ├── index.ts                  # re-exports + API surface
│       └── electron.ts               # implementation via window.electronAPI
├── electron/                         # NEW (E1–E3): Electron shell
│   ├── main/
│   │   ├── index.ts                  # app lifecycle, BrowserWindow, single-instance
│   │   ├── sidecar.ts                # child_process.spawn, port/vault capture
│   │   ├── vault-state.ts            # assertVaultPath(), runtime FS scope
│   │   ├── ipc.ts                    # all ipcMain.handle() registrations
│   │   ├── protocols.ts              # app:// and analecta-file:// protocol handlers
│   │   ├── tray.ts                   # Tray icon + menu
│   │   └── updater.ts                # electron-updater setup
│   ├── preload/
│   │   └── index.ts                  # contextBridge, ALLOWED_CHANNELS whitelist
│   ├── package.json
│   └── tsconfig.json
├── binaries/                         # NEW (E8): sidecar output (moved from src-tauri/)
│   └── analecta-sidecar/             # PyInstaller onedir — gitignored
├── scripts/
│   ├── build_sidecar.py              # updated in E8: output → binaries/ (was src-tauri/binaries/)
│   ├── dev.py                        # unchanged
│   └── system_deps.sh                # unchanged
├── electron-builder.yml              # NEW (E6): packaging config
├── pnpm-workspace.yaml               # updated (E1): add electron package
├── package.json                      # updated (E1): scripts for electron dev/build
└── src-tauri/                        # DELETED in E8
```

---

## IPC Surface (complete list)

Defined in `electron/preload/index.ts` `ALLOWED_CHANNELS` and `electron/main/ipc.ts`.

| Channel | Direction | Handler |
|---------|-----------|---------|
| `get-sidecar-port` | invoke | Returns cached port from sidecar stdout |
| `update-vault-scope` | invoke | Calls `setVaultPath(path)` in vault-state |
| `read-file` | invoke | `fs.readFile` — assertVaultPath required |
| `write-file` | invoke | `fs.writeFile` — assertVaultPath required |
| `file-exists` | invoke | `fs.access` — assertExistsPath only |
| `open-dialog` | invoke | `dialog.showOpenDialog` |
| `show-message-box` | invoke | `dialog.showMessageBox` |
| `open-url` | invoke | `shell.openExternal` — http/https only |
| `reveal-in-dir` | invoke | `shell.showItemInFolder` |
| `clipboard-read` | invoke | `clipboard.readText()` |
| `notify` | invoke | `new Notification(title, {body})` |
| `check-update` | invoke | `autoUpdater.checkForUpdates()` |
| `download-and-install-update` | invoke | `autoUpdater.downloadUpdate()` then `quitAndInstall()` |
| `relaunch` | invoke | `app.relaunch(); app.exit(0)` |
| `get-login-item` | invoke | `app.getLoginItemSettings().openAtLogin` |
| `set-login-item` | invoke | `app.setLoginItemSettings({openAtLogin})` |
| `get-initial-deep-link` | invoke | Returns URL received before renderer was ready |
| `sidecar-ready` | on (event) | Emitted by main when sidecar prints `SIDECAR_READY` |
| `deep-link` | on (event) | Emitted by main on `analecta://` URL received |

---

## @tauri-apps Imports to Replace (E5)

10 files, 15 import lines.

| File | Tauri import | Platform shim replacement |
|------|-------------|--------------------------|
| `+layout.svelte` | `invoke` from `@tauri-apps/api/core` | `platform.getSidecarPort()`, `platform.updateVaultScope()` |
| `+layout.svelte` | `listen` from `@tauri-apps/api/event` | `platform.onSidecarReady()` |
| `+layout.svelte` | `getCurrent, onOpenUrl` from `plugin-deep-link` | `platform.onDeepLink()` |
| `+layout.svelte` | `Update` type, dynamic `plugin-updater` import | `platform.checkUpdate()` |
| `UpdateBanner.svelte` | `Update` type, `relaunch` from `plugin-process` | `platform.relaunch()` |
| `first-run/+page.svelte` | `invoke` from `@tauri-apps/api/core` | `platform.updateVaultScope()` |
| `first-run/+page.svelte` | `open as openDialog` from `plugin-dialog` | `platform.openDialog()` |
| `ContextMenu.svelte` | `revealItemInDir` from `plugin-opener` | `platform.revealInDir()` |
| `ContextMenu.svelte` | `confirm` from `plugin-dialog` | `platform.confirm()` |
| `+page.svelte` | `exists` from `plugin-fs` | `platform.fileExists()` |
| `editor/[id]/+page.svelte` | `readTextFile, writeTextFile` from `plugin-fs` | `platform.readTextFile()`, `platform.writeTextFile()` |
| `viewer/[id]/+page.svelte` | `openUrl` from `plugin-opener` | `platform.openUrl()` |
| `viewer/[id]/+page.svelte` | `confirm` from `plugin-dialog` | `platform.confirm()` |
| `viewer/[id]/+page.svelte` | `readTextFile` from `plugin-fs` | `platform.readTextFile()` |
| `markdown/renderer.ts` | `convertFileSrc` from `@tauri-apps/api/core` | `platform.convertFileSrc()` → `analecta-file://${path}` |
| `Sidebar.svelte` | `readText` from `plugin-clipboard-manager` | `platform.clipboardReadText()` |
| `settings/+page.svelte` | `invoke` from `@tauri-apps/api/core` | `platform.updateVaultScope()` |
| `settings/+page.svelte` | `open as openDialog` from `plugin-dialog` | `platform.openDialog()` |

---

## Migration Blocks

Each block is atomic, sequential, and ends with a verification criterion.

---

### E1 — electron/ package setup

**Files:** `electron/package.json`, `electron/tsconfig.json`, `pnpm-workspace.yaml`,
root `package.json`.

**Changes:**
- Create `electron/package.json`: name `analecta-electron`, private, main
  `dist/main/index.js`. Dev deps: `electron@^42.0.0`, `electron-builder`, `typescript`,
  `@types/node`. Deps: `electron-updater`, `@electron-toolkit/preload`,
  `@electron-toolkit/utils`.
- Create `electron/tsconfig.json`: `target: ES2022`, `module: CommonJS`,
  `outDir: dist`, `rootDir: src` (covering `main/` and `preload/`),
  `strict: true`, `sourceMap: true`.
- Update `pnpm-workspace.yaml`: add `- electron`.
- Update root `package.json` scripts:
  ```json
  "electron:dev": "pnpm --filter electron run dev",
  "electron:build": "pnpm --filter electron run build",
  "dist": "pnpm --filter frontend build && python scripts/build_sidecar.py && pnpm --filter electron run dist"
  ```
- Add `electron/package.json` scripts: `"build": "tsc"`,
  `"dev": "VITE_DEV_SERVER_URL=http://localhost:5173 electron dist/main/index.js"`,
  `"dist": "electron-builder"`.

**Verification:** `mise exec -- pnpm --filter electron run build` compiles with 0 TS errors
(empty source files are fine at this stage). `mise exec -- pnpm install` resolves the
workspace with no conflicts.

---

### E2 — Main process

**Files:** `electron/main/index.ts`, `electron/main/sidecar.ts`,
`electron/main/vault-state.ts`, `electron/main/ipc.ts`,
`electron/main/protocols.ts`, `electron/main/tray.ts`, `electron/main/updater.ts`.

**`index.ts`:**
- `app.requestSingleInstanceLock()` — if false, quit immediately.
- `second-instance` callback: re-focus window; if `argv` contains `analecta://`,
  parse and emit `deep-link` to renderer.
- `app.setAsDefaultProtocolClient('analecta')` on Linux/macOS.
- `registerProtocols()` before `app.ready` (from `protocols.ts`).
- On `app.ready`: create `BrowserWindow` with hardened `webPreferences` (see
  `docs/electron-shell-security.md` Layer 1), register IPC handlers, spawn sidecar,
  load frontend URL (dev: `VITE_DEV_SERVER_URL`, prod: `app://index.html`).
- `app.on('window-all-closed')`: `app.quit()` on non-macOS; kill sidecar.
- `app.on('before-quit')`: kill sidecar child process.
- **Wayland native mode**: Analecta runs Wayland-native by default (no `--ozone-platform=x11`).
  Export `const isWaylandNative = process.env.XDG_SESSION_TYPE === 'wayland'` for use in `ipc.ts`.
  Window focus is best-effort: always call `mainWindow.show()` before `mainWindow.focus()`.
  `focus()` may flash the taskbar instead of raising on Wayland — this is acceptable.

**`sidecar.ts`:**
- `spawnSidecar()`: determine binary path — dev: `src-tauri/binaries/analecta-sidecar/analecta-sidecar`
  relative to repo root; packaged: `path.join(process.resourcesPath, 'analecta-sidecar', 'analecta-sidecar')`.
- `spawn(bin, [], { stdio: 'pipe' })`. Parse stdout lines:
  - `LISTENING_ON_PORT:<n>` → store port, call `getSidecarPort()` resolvers.
  - `VAULT_PATH:<path>` → call `setVaultPath(path)` in vault-state.
  - `SIDECAR_READY` → emit `sidecar-ready` to all windows.
- Pipe stderr to `console.error('[sidecar]', line)`.
- `killSidecar()`: `child.kill('SIGTERM')` with 3 s SIGKILL fallback.

**`vault-state.ts`:**
- `let vaultPath: string | null = null`.
- `setVaultPath(p)`: resolves to `path.resolve(p)`.
- `assertVaultPath(filePath)`: resolves filePath, checks it starts with `vaultPath + path.sep`
  or equals `vaultPath`. Throws `Error('path outside vault')` otherwise.
- `assertExistsPath(filePath)`: checks non-empty string only (no vault restriction).
- Export `addAllowedFontPath(p)` for font files selected via dialog.

**`ipc.ts`:** Register all handlers listed in the IPC Surface table above.
Every string argument from the renderer is validated before use. Filesystem paths go
through `assertVaultPath` or `assertExistsPath`. `open-url` rejects non-http(s) schemes.
See `docs/electron-shell-security.md` Layer 3 for the full validation rules.
- **`open-dialog` handler**: wrap `dialog.showOpenDialog()` in an 8 s `Promise.race` timeout.
  On timeout or rejection, throw `new Error('dialog-unavailable')` — the renderer catches
  this and falls back to a manual text-input field. This mitigation covers the FileChooser
  portal SIGSEGV on COSMIC Wayland (cosmic-epoch#3467).

**`protocols.ts`:**
- `app://` — registered with `{ standard: true, secure: true, supportFetchAPI: true }`.
  Serves files from `frontend/build/`. Returns 404 for paths outside that directory.
- `analecta-file://` — serves vault image files. Applies vault path restriction
  (assertVaultPath) and extension allowlist (`.png .jpg .jpeg .gif .webp .svg .avif`).
  Returns 403 on violation.
- Both registered with `protocol.registerSchemesAsPrivileged()` before `app.ready`.
- CSP injected via `session.defaultSession.webRequest.onHeadersReceived` — see
  `docs/electron-shell-security.md` Layer 5 for the exact policy string.

**`tray.ts`:**
- `Tray` with icon from `electron/build-resources/tray-icon.png`.
- Menu: "Add URL from clipboard" (reads clipboard → POST /api/v1/extract),
  "Open Analecta" (shows/focuses window), "Start with system" (toggle via
  `app.setLoginItemSettings`), "Quit" (`app.quit()`).
- Double-click: `mainWindow.show()`.

**`updater.ts`:**
- Import `autoUpdater` from `electron-updater`.
- `initUpdater(win)`: configure feed URL from electron-builder publish config.
  Emit `update-available` to window on new release. Expose `checkForUpdates()`,
  `downloadUpdate()`, `quitAndInstall()` called from IPC handlers.

**Verification:** `pnpm electron:dev` (with Vite dev server and sidecar already running)
opens a window, loads the SvelteKit UI. DevTools console shows `sidecar-ready` event.
`fetch('http://localhost:' + port + '/api/v1/system/health')` returns 200.

---

### E3 — Preload

**File:** `electron/preload/index.ts`.

**Changes:**
- No Node.js modules except `contextBridge` and `ipcRenderer` from `electron`.
- `ALLOWED_CHANNELS` const array — see IPC Surface table above.
- `contextBridge.exposeInMainWorld('electronAPI', { invoke, on })` where:
  - `invoke(channel, ...args)`: checks channel against `ALLOWED_CHANNELS`, throws if not found,
    then calls `ipcRenderer.invoke(channel, ...args)`.
  - `on(channel, callback)`: guards `sidecar-ready` and `deep-link` only;
    returns an unsubscribe function that calls `ipcRenderer.removeListener`.
- Global type declaration: `interface Window { electronAPI: typeof api }`.

**Verification:** `pnpm --filter electron run build` produces `electron/dist/preload/index.js`
with no TS errors. In DevTools, `window.electronAPI` is defined and `invoke('unknown')` throws.

---

### E4 — Frontend platform shim

**Files:** `frontend/src/lib/platform/index.ts`, `frontend/src/lib/platform/electron.ts`.

**Changes:**

`electron.ts` — wraps every `window.electronAPI.invoke` and `.on` call with a
typed function. API surface (all returns are `Promise<T>` unless noted):

```ts
readTextFile(path: string): Promise<string>
writeTextFile(path: string, data: string): Promise<void>
fileExists(path: string): Promise<boolean>
openDialog(opts: OpenDialogOptions): Promise<string | null>
confirm(message: string, title?: string): Promise<boolean>
openUrl(url: string): Promise<void>
revealInDir(path: string): Promise<void>
clipboardReadText(): Promise<string>
notify(title: string, body: string): Promise<void>
relaunch(): Promise<void>
checkUpdate(): Promise<void>
downloadAndInstallUpdate(): Promise<void>
getLoginItem(): Promise<boolean>
setLoginItem(value: boolean): Promise<void>
convertFileSrc(path: string): string          // sync — returns analecta-file://{path}
onSidecarReady(cb: (port: number) => void): () => void
onDeepLink(cb: (url: string) => void): () => void
getInitialDeepLink(): Promise<string | null>
updateVaultScope(path: string): Promise<void>
```

`index.ts` — re-exports `electron.ts`. This file is the import target across the
entire frontend; if a second platform (e.g., web) is added later, only this file
changes.

**Verification:** `mise exec -- pnpm --filter frontend check` (svelte-check) passes with
0 errors on the new files. No @tauri-apps imports in `platform/`.

---

### E5 — Replace @tauri-apps imports in the frontend

**Files:** 10 files listed in the @tauri-apps Imports table above.

**Changes:** Replace every `@tauri-apps/*` import with the corresponding call from
`$lib/platform`. The table above is the complete substitution map.

Additional notes:
- `UpdateBanner.svelte`: the `Update` type is no longer needed — electron-updater state is
  managed by the main process. The banner listens for an `update-available` IPC event and
  calls `platform.downloadAndInstallUpdate()`.
- `ContextMenu.svelte`: `confirm()` from `plugin-dialog` → `platform.confirm()` which calls
  `show-message-box` with `type: 'question'` and two buttons.
- `markdown/renderer.ts`: `convertFileSrc(path)` → `platform.convertFileSrc(path)` which
  returns `analecta-file://${path}`.

Remove all `@tauri-apps/*` packages from `frontend/package.json` after replacing the imports.

**Verification:** `grep -r "@tauri-apps" frontend/src/` returns no results.
`mise exec -- pnpm --filter frontend check` passes with 0 errors.
`mise exec -- pnpm --filter frontend build` succeeds.

---

### E6 — electron-builder packaging

**Files:** `electron-builder.yml` (repo root), `electron/package.json` (dist script),
`scripts/build_sidecar.py` (no change yet — sidecar still in `src-tauri/binaries/`).

**`electron-builder.yml`:**

```yaml
appId: io.analecta.desktop
productName: Analecta

directories:
  buildResources: electron/build-resources
  output: dist-electron

files:
  - electron/dist/**/*
  - "!electron/dist/**/*.map"

extraResources:
  - from: src-tauri/binaries/analecta-sidecar
    to: analecta-sidecar
    filter: ["**/*"]
  - from: frontend/build
    to: frontend-build

linux:
  target:
    - deb
    - rpm
    - AppImage
  icon: electron/build-resources/icons
  category: Utility
  desktop:
    MimeType: x-scheme-handler/analecta

publish:
  provider: github
  owner: E-zequiel
  repo: analecta
```

**`electron/package.json` dist script:**
```json
"dist": "electron-builder --config ../electron-builder.yml"
```

**Signing:** electron-updater requires code signing for delta updates on macOS. On Linux,
signing is optional but good practice. The signing key for electron-builder replaces
`TAURI_SIGNING_PRIVATE_KEY`. Create new BSM secrets (see `docs/bitwarden-secrets-manager.md`
pending note) once the exact electron-builder signing config is determined.

**Verification:** `mise exec -- pnpm dist` (after running `scripts/build_sidecar.py`)
produces `dist-electron/` with `.deb`, `.rpm`, `.AppImage`. Install `.deb`:
`sudo dpkg -i dist-electron/Analecta_*.deb && analecta` — app opens, sidecar starts,
dashboard loads.

---

### E7 — CI/CD update

**Files:** `.github/workflows/release.yml`, `.github/workflows/ci.yml`,
`.github/dependabot.yml`.

**`release.yml` changes:**
- Remove `tauri-apps/tauri-action` step and its SHA from the inventory.
- Replace with: `mise exec -- python scripts/build_sidecar.py` then
  `mise exec -- pnpm --filter electron run build` then `mise exec -- pnpm dist`.
- Upload `.deb`/`.rpm`/`.AppImage` assets from `dist-electron/` to the GitHub Release
  using `actions/upload-release-asset` or the `gh` CLI.
- Update BSM secrets injection step: replace Tauri signing vars with electron-builder
  equivalents once E6 is finalised.
- Remove Rust toolchain setup step (no longer needed after src-tauri is gone).
- Update the action inventory table in `docs/github-actions-security.md`.

**`ci.yml` changes:**
- Remove the `cargo check` job (src-tauri still present but Rust is no longer part of
  the product). Or keep it until E8 — either is acceptable.

**`dependabot.yml` changes:**
- Remove the `cargo` ecosystem block.

**`docs/github-actions-security.md`:** Remove the pending marker from Control 1 and
update the action inventory with the real SHA of the new action (resolve via `curl`).

**`docs/bitwarden-secrets-manager.md`:** Remove the pending marker and update the
secrets inventory for the new signing key name.

**Verification:** Push a test tag `v0.3.0-alpha.1`; workflow runs green and produces a
GitHub Release draft with `.deb`/`.AppImage`/`.rpm` assets.

---

### E8 — Delete src-tauri/

**Files:** `src-tauri/` (delete), `scripts/build_sidecar.py` (update output path),
`electron-builder.yml` (update `extraResources` from), `scripts/check.sh` (remove Rust steps),
`.mise.toml` (remove Rust toolchain), root `package.json` (remove `tauri` scripts),
`CLAUDE.md` (remove remaining `src-tauri/` references).

**Changes:**
- `rm -rf src-tauri/`
- `scripts/build_sidecar.py`: change `distpath` from `src-tauri/binaries` to `binaries`
  (repo root). Create `binaries/` in `.gitignore`.
- `electron-builder.yml`: update `extraResources.from` to `binaries/analecta-sidecar`.
- `scripts/check.sh`: remove the "Rust shell" section (steps 5–7 + the sidecar stub logic).
  Remove the `> Pending (post-E8)` marker from `docs/quality-gate.md` and delete the
  entire Rust section from that doc.
- `.mise.toml`: remove `rust = "stable"`.
- Root `package.json`: remove any `tauri` or `cargo` scripts.
- `CLAUDE.md`: remove `src-tauri/` from the Source Layout, remove `Rust stable` from the
  toolchain section, update the shell column from Tauri to Electron.

**Verification:** `ls src-tauri/` → "No such file or directory".
`./scripts/check.sh` passes with no Cargo steps. `mise exec -- pnpm dist` still produces
working installers (sidecar now comes from `binaries/`).

---

### E9 — Verification and merge

**QA checklist (manual, full session):**

1. `./scripts/check.sh` passes clean.
2. Fresh `.deb` install on Pop!_OS 24.04 from a clean user account.
3. First-run dialog → set vault path → dashboard loads empty.
4. Add 5 entries: 2 articles, 1 YouTube, 1 Substack, 1 that triggers VirusTotal.
5. All entries appear in dashboard via SSE without manual reload.
6. Open viewer: Markdown renders, local images load via `analecta-file://`.
7. Viewer toolbar: Open (browser), Open in file manager, Copy `analecta://` URL.
8. Editor: save edits → hashtags update sidebar.
9. Search (Ctrl+K): full-text results correct.
10. `xdg-open analecta://open?id=1` → app focuses, viewer opens correct entry.
11. Tray: "Add URL from clipboard" → entry created.
12. Tray: "Start with system" toggle persists across session.
13. electron-updater: simulate update (mock server or test tag) → banner appears,
    restart applies update.
14. Close window → tray persists. Quit → `ps aux | grep analecta` = empty (no orphan sidecar).
15. Security test: attempt `read-file` on `~/.ssh/id_rsa` from DevTools console via
    `window.electronAPI.invoke('read-file', '/home/user/.ssh/id_rsa')` → rejected.

**After QA passes:**
- Remove the `> Pending (post-E7)` marker from `docs/github-actions-security.md`
  (already done in E7 above — double-check).
- Update `docs/pnpm-decision.md` workspace section if `electron` was not yet added.
- Open PR `feat/electron-shell` → `main`. Rebase and merge.
- Tag `v0.3.0`.
- Post-merge: begin `docs/` final polish pass (deferred from pre-E1 cleanup).

---

## Risk Register

| Risk | Mitigation |
|------|-----------|
| `dialog.showOpenDialog()` on Wayland/COSMIC: FileChooser portal SIGSEGV after first use (cosmic-epoch#3467) | 8 s timeout wrapper in `ipc.ts` `open-dialog` handler; renderer shows manual text-input fallback on timeout/rejection. Implemented in E2. |
| `win.focus()` on deep-link or tray click is best-effort on Wayland | Always call `show()` first; `focus()` may flash taskbar. Window is visible; focus is best-effort. Acceptable UX, no workaround. |
| Multi-monitor window positioning (electron#48749, Wayland-only, closed "not planned") | Wayland compositor controls window placement. Analecta does not use programmatic coordinates — not applicable. |
| Tray icon on COSMIC requires AppIndicator support | Document as known limitation; verify in E9 |
| Renderer compromise via markdown-it XSS | Six-layer security model (E2/E3) replicates Tauri capability boundaries |
| New IPC handlers added without validation | Security checklist in `docs/electron-shell-security.md` — required review gate |
