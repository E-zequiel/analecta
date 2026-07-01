# Wayland Desktop Icon: Taskbar and Alt-Tab Switcher

**Status:** Implemented — confirmed working in dev (2026-07-01)
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

Gated on `!app.isPackaged && process.platform === 'linux'`, before `app.whenReady()`:

1. `app.setDesktopName('analecta-dev.desktop')` — aligns the Wayland `xdg_toplevel` app-id with a dev-only `.desktop` filename. `setDesktopName` is present in the Electron 42 runtime binary but missing from its TypeScript types (`setDesktopFileName` does **not** exist at all in E42); a cast is required.
2. `registerDevSchemeHandler()` writes `~/.local/share/applications/analecta-dev.desktop` with `Icon=<absolute-path-to-512x512.png>` and `StartupWMClass=Analecta`, then runs `update-desktop-database` to refresh the compositor's cache.

Both steps must run before the first window opens — `app.getPath('home')` and `app.getAppPath()` are safe to call pre-ready.

**Packaged builds skip this entirely.** `electron-builder` (`electron-builder.yml`, `linux.executableName: analecta`) generates its own `analecta.desktop` and the running binary is already named `analecta`, so the app-id and `.desktop` filename match automatically — no `setDesktopName` call needed.

---

## Scope

Confirmed working in dev on **COSMIC (Pop!_OS 24.04, Wayland native)** as of 2026-07-01. The packaged-build path has not yet been verified against a real `.deb`/`.rpm` artifact — local `fpm` packaging is blocked by a path-corruption bug (`/mnt/HD_ARCHIVO` → `/mnt/HD_amd64IVO`), so this requires a CI-built artifact to confirm.
