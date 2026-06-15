# Decision: Migrate Shell from Tauri 2.x to Electron

**Status:** Accepted  
**Date:** 2026-05-18  
**Context:** `feat/electron-shell` — replaces the Tauri 2.x desktop shell; Python sidecar and SvelteKit frontend are unchanged

---

## Context

Analecta is a read-it-later PKM application. Its primary use case is extended reading of extracted articles — typography quality is not cosmetic, it is functional. A reader spending 30–60 minutes per session will perceive rendering differences that would be invisible in a utility app opened briefly.

Since the completion of the hybrid architecture (Tauri 2.x + FastAPI sidecar + SvelteKit), a persistent visual quality gap exists between Analecta and Obsidian on Linux Wayland. Both applications render the same font (JetBrains Mono), the same CSS `font-weight` values, and the same color palette — yet Obsidian's text is noticeably sharper, with better contrast and more precise letterforms.

---

## Root Cause: Font Rasterizer Difference

The gap has a single architectural cause: the font rasterization pipeline.

| Component | Rasterizer | Hinting source |
|-----------|-----------|---------------|
| Tauri 2.x (WebKitGTK on Linux) | FreeType | Fontconfig (system-level) |
| Electron / Chromium 133+ | Skrifa (Fontations) | Internal — ignores Fontconfig |
| Obsidian | Skrifa (Fontations) | Internal — ignores Fontconfig |

**Skrifa**, introduced in Chrome 133 (February 2025) and now the default rasterizer in all Chromium-based applications, implements its own hinting algorithms independently of the operating system's font configuration. On Linux Wayland with dark backgrounds, Skrifa produces consistently sharper glyph outlines than FreeType at typical reading sizes (14–18 px).

A secondary symptom also caused by this difference: the `font-weight` +100 rendering bias in WebKitGTK (open issue `tauri-apps/tauri#14286`), where a declared `font-weight: 400` renders visually as 500. Analecta works around this by declaring `300`/`600` instead of `400`/`700`. This workaround is unnecessary in Skrifa, which renders declared weights with full CSS fidelity.

This is not a Tauri bug. It is an inherent property of the WebKitGTK/FreeType rendering stack on Linux.

---

## Why Fontconfig Is Not a Viable Solution

Adjusting `~/.config/fontconfig/fonts.conf` (hinting mode, LCD filter, antialiasing) can improve FreeType output but cannot close the gap with Skrifa for two reasons:

1. **Skrifa ignores Fontconfig entirely.** The two rasterizers implement different algorithms; there is no configuration that makes FreeType produce Skrifa-equivalent output.
2. **User-side configuration is not acceptable for a distributed application.** Requiring end users to edit `fonts.conf` before Analecta renders correctly introduces an invisible, undocumented prerequisite. A packaged application must look correct out of the box.

---

## Decision

**Migrate the application shell from Tauri 2.x to Electron 42.x.**

The Python FastAPI sidecar and the SvelteKit frontend are unchanged in logic. Only the desktop shell layer (previously `src-tauri/`) is replaced by a TypeScript Electron main process (`electron/`). See [`electron-shell-security.md`](electron-shell-security.md) for implementation details.

---

## What We Gain

### Primary motivation

**G1 — Font rendering identical to Obsidian.** Electron packages Chromium. Chromium uses Skrifa. After migration, Analecta and Obsidian share the same glyph rasterizer, hinting algorithms, and font metrics. The `font-weight` compensation hack becomes unnecessary.

### Secondary benefits

**G2 — Correct fractional scaling on Wayland.** Electron implements the `wp-fractional-scale-v1` protocol. Non-integer scale factors (1.25×, 1.5×) render at native pixel density without the bilinear rescaling imposed by XWayland.

**G3 — Wayland native by default.** No `GDK_BACKEND=wayland`, no `--ozone-platform` flags, no environment variable workarounds. COSMIC (Smithay compositor) is fully compatible with Electron 42.x out of the box.

**G4 — Client-side window decorations on Wayland.** Electron 41+ supports CSD, enabling the window frame to be integrated with the application's visual design — the same approach used by VS Code and Obsidian.

**G5 — Cross-platform rendering consistency.** Chromium ships the same rasterizer on Linux, macOS, and Windows. The entire class of "renders differently on Linux" bugs that is endemic to Tauri (where the WebView backend varies per OS) is eliminated.

**G6 — Mature ecosystem.** electron-builder, electron-updater (differential updates), Crashpad/Sentry crash reporting, and a decade of production use across VS Code, Slack, Discord, Figma, and Obsidian.

**G8 — Full Chrome DevTools.** Performance, Memory, Network, and Layers panels behave identically to the browser. Profiling and memory leak detection are significantly more ergonomic than WebKitGTK Inspector.

**G9 — Hardware-accelerated Wayland rendering.** Native Wayland eliminates the XWayland compositing layer, reducing GPU process latency and enabling VRR/FreeSync when hardware and compositor support it.

**G10 — Wayland CI in Electron.** Since February 2026, Electron runs a dedicated Wayland test job on every release, committing the team to not regressing Wayland support.

---

## Trade-offs We Accept

### P1 — RAM: 5–8× higher idle usage

| State | Tauri 2.x + sidecar | Electron + sidecar |
|-------|--------------------|--------------------|
| Idle | ~80–150 MB total | ~200–400 MB total |
| Active | ~100–200 MB total | ~300–500 MB total |

**Rationale for a PKM application:** Analecta is opened once per work session and left running during hours of reading. The additional RAM is a fixed per-session cost, not a per-operation cost. On any system where a PKM reading workflow is practical (≥ 8 GB RAM), 300 MB for a primary reading application is within normal expectations — Obsidian's footprint is identical. The Python sidecar already contributed 50–100 MB to the baseline; the system was never comparable to a lightweight utility.

### P2 — Installer size: 10–15× larger

| Format | Tauri 2.x | Electron |
|--------|-----------|---------|
| Compressed installer | < 10 MB | 80–150 MB |
| Installed on disk | 15–30 MB | 200–300 MB |

**Rationale:** Analecta is distributed via GitHub Releases (`.deb`, `.rpm`, `.AppImage`). For the target user — a developer running a local PKM workflow — a 100–150 MB one-time download is not a barrier. electron-updater delivers binary-delta updates for incremental releases, keeping ongoing download sizes small. Obsidian on Linux is approximately 130 MB, establishing user expectations for this software category.

### P3 — Startup: ~1–2 s additional overhead

**Rationale:** Analecta already displays a loading screen while the Python sidecar initializes (1.5–3 s for the PyInstaller bundle). Electron's startup overhead runs largely in parallel with sidecar initialization. The user-visible wait before the application is usable is nearly unchanged — the sidecar cold-start dominates in both architectures.

---

## Risks We Mitigate

### P6 — Electron's permissive security defaults

Tauri enforces a capability model at the framework level: a renderer cannot invoke any native API unless it is explicitly declared in `capabilities/`. Electron has no equivalent system — the renderer process can access the full Node.js API surface unless the developer explicitly restricts it.

**Our mitigation:** A six-layer security model replicates Tauri's guarantees in Electron's imperative programming model. The model is described fully in [`electron-shell-security.md`](electron-shell-security.md). At a high level:

- `contextIsolation: true` + `sandbox: true` + `nodeIntegration: false` on every `BrowserWindow`
- Vault-scoped filesystem validation via `vault-state.ts` (equivalent to Tauri's FS capability grant)
- Type validation and input sanitization on every IPC handler
- Path and extension restrictions on the `analecta-file://` protocol handler
- Strict Content Security Policy applied via session header interceptor
- Explicit channel whitelist in the preload script

**Ongoing discipline:** Unlike Tauri's declarative model, Electron's security requires that every new IPC handler added in the future follows the validation pattern. This is a developer discipline requirement, not a framework guarantee. See the developer guidelines in [`electron-shell-security.md`](electron-shell-security.md).

---

## Known Remaining Limitations

These are Wayland protocol constraints that cannot be resolved at the application level.

| # | Limitation | Impact on Analecta |
|---|-----------|-------------------|
| P10 | `win.focus()` without a valid xdg-activation token is ignored by the compositor (Wayland protocol restriction) | Deep-link handler may not foreground the window; the link is still processed and navigation occurs correctly. Tray-initiated clipboard access removed — see [`wayland-tray-focus.md`](wayland-tray-focus.md) |
| P12 | System tray icon requires AppIndicator support from the compositor panel | Tray icon visibility on COSMIC depends on the panel version; must be verified early in testing |
| P15 | Multi-monitor window positioning bug in Electron ≥ 38.4 ([#48749](https://github.com/electron/electron/issues/48749)) | Window may open on the wrong monitor in multi-display setups; workaround: launch with `--ozone-platform=x11` |

---

## Why This Migration Is Low-Cost for Analecta

The hybrid architecture was designed with a strict separation of concerns that makes this shell replacement unusually inexpensive:

| Component | Migration cost | Reason |
|-----------|---------------|--------|
| Python FastAPI sidecar | **Zero** | Independent subprocess; Electron spawns it identically to Tauri |
| SvelteKit frontend | **Near zero** | Plain HTML/CSS/JS running unchanged in Chromium; import paths change, logic does not |
| Rust shell (`src-tauri/`) | **Low** | ~300 lines of lifecycle scaffolding with no business logic |
| Tauri plugin APIs | **Low** | ~5 plugins replaced by Node.js built-ins exposed through `contextBridge` |
| SSE from Python backend | **Zero** | Standard HTTP-based EventSource; no Tauri involvement |

The Python sidecar was always the location of all business logic. The SvelteKit frontend was always decoupled from the shell. The Rust code was always a thin process manager. This architectural discipline is what makes the migration feasible within a single sprint.

---

## Wayland Runtime Notes

Implementation details for Wayland-specific behaviors are tracked in dedicated documents:

- [`wayland-tiling-wm.md`](wayland-tiling-wm.md) — Unmaximize gap in COSMIC tiling (workaround in `electron/main/index.ts`)
- [`wayland-tray-focus.md`](wayland-tray-focus.md) — Tray focus and clipboard restrictions; research, workaround history, and removal rationale

---

## Discarded Alternatives

**Fontconfig system tuning** — Improves FreeType rendering but does not match Skrifa's output, and requires user-side configuration that cannot be part of a packaged application.

**Stay on Tauri and wait for WebKitGTK to adopt Skrifa** — WebKitGTK has no announced plans to replace FreeType with Skrifa. The upstream bug `tauri-apps/tauri#14286` has been open since 2024 with no resolution path. The rendering gap is structural to the WebKitGTK/FreeType stack.

**Tauri with a pluggable WebView** — Not supported by Tauri 2.x. The WebView backend is not configurable at the application level.

**Alternative frameworks (Neutralinojs, Wails, Sciter)** — Higher migration cost, smaller ecosystems, weaker Wayland support guarantees, and no path to Skrifa-based rendering.
