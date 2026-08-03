# Wayland Desktop Icon: Taskbar and Alt-Tab Switcher

**Status:** Implemented — confirmed working in dev and in the packaged build (taskbar and alt-tab both correct on a CI-built `v0.5.0` install)
**Date:** 2026-06-30

---

## Problem

On Wayland, the Analecta window showed no icon (or a generic one) in the running-apps taskbar and the alt-tab switcher.

---

## Root Cause

Wayland-native Electron does not use `_NET_WM_ICON` (an X11 mechanism). The compositor resolves an app's icon by matching the `xdg_toplevel` app-id against a `.desktop` file of the same name, then reading that file's `Icon=` field.

The taskbar and the alt-tab switcher are resolved independently:

- **Taskbar** (running-apps bar): reads `BrowserWindow`'s own icon directly.
- **Alt-tab switcher**: reads the compositor-level app-id → `.desktop` → `Icon=` chain described above.

In dev, the running binary is `electron`, not `analecta`, so there is no matching `.desktop` file and the alt-tab lookup fails.

---

## Solution

**Implemented in `electron/main/index.ts`.**

### Taskbar icon

Set `icon: resolveAppIcon()` on `BrowserWindow`. The icon path resolves differently in dev vs. packaged builds; the packaged path is shipped via `extraResources` in `electron-builder.yml` (`build-resources/icons/512x512.png` → `icon.png`, read back via `process.resourcesPath`).

### Alt-tab icon

Gated on `process.platform === 'linux'`, before `app.whenReady()`:

1. `app.setDesktopName(...)` — aligns the Wayland `xdg_toplevel` app-id with the installed `.desktop` filename: `analecta-dev.desktop` in dev, `analecta.desktop` when packaged. `setDesktopName` is present in the Electron 42 runtime binary but missing from its TypeScript types (`setDesktopFileName` does **not** exist at all in E42); a cast is required.
2. Dev only: `registerDevSchemeHandler()` writes `~/.local/share/applications/analecta-dev.desktop` with `Icon=<absolute-path-to-512x512.png>` and `StartupWMClass=Analecta`, then runs `update-desktop-database` to refresh the compositor's cache. Packaged builds skip this — `electron-builder` (`electron-builder.yml`, `linux.executableName: analecta`) already generates and installs `analecta.desktop`.

All dev-only steps must run before the first window opens — `app.getPath('home')` and `app.getAppPath()` are safe to call pre-ready.

**Packaged builds need the `setDesktopName` call too.** The original assumption here was that a matching running-binary name and `.desktop` filename would make Electron's Wayland app-id resolve automatically, without an explicit call. **Disproven**: a real CI-built `.deb`, installed and traced with `WAYLAND_DEBUG=1`, showed `xdg_toplevel.set_app_id("analecta-electron")` — Electron falls back to the raw `electron/package.json` `"name"` field regardless of the installed binary/`.desktop` filename. Same underlying leak as the `.deb` `Package:` field, fixed separately via `electron-builder.yml`'s `deb.packageName` (see `docs/electron-builder-linux-package-naming.md`). Fixed by calling `setDesktopName('analecta.desktop')` unconditionally for packaged Linux builds too, not just dev.

---

## Scope

Confirmed working in dev on **COSMIC (Pop!_OS 24.04, Wayland native)**, and confirmed again against a real CI-built `.deb` install — taskbar and alt-tab switcher both resolve the correct icon.
