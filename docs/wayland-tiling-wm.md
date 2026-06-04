# Wayland Tiling WM: Unmaximize Gap (COSMIC / Pop!_OS)

**Status:** Implemented  
**Date:** 2026-06-04

---

## Problem

After using a WM-level maximize shortcut (e.g. Super+M in COSMIC Pop Shell) to maximize and then restore a window that was moved to a different tile, a visual gap appears on the right and bottom edges. The window occupies less space than its assigned tile. Clicking any other window in the workspace resolves it immediately.

---

## Root Cause

On WM-initiated unmaximize, COSMIC configures the window at the pre-move (stale) tile bounds rather than the current tile bounds. Electron's `getBounds()` reflects the wrong size and Chromium never commits a `wl_buffer` at the correct dimensions.

The application-initiated path (`win.unmaximize()`) is unaffected: Electron drives the full `xdg_toplevel` configure cycle itself and the correct bounds are always committed.

---

## Solution

**Implemented in `electron/main/index.ts`.**

Track the last normal size (`width`/`height` only — position is left to the compositor) via `resize`/`move` events debounced at 50 ms to avoid capturing intermediate tile-animation frames. After unmaximize settles (150 ms debounce), if the window is not already re-maximized and the current size diverges from the saved normal size, call `setSize(w, h)`.

Using `setSize` instead of `setBounds` avoids overriding the compositor's tile-placement decision. On well-behaved compositors (GNOME/Mutter) the saved and current sizes always match, so `setSize` is never called — the guard is a safe no-op on those systems.

The workaround is gated on `isWaylandNative` (`XDG_SESSION_TYPE === wayland`).

---

## Scope

Confirmed on **COSMIC (Pop!_OS 24.04)**. May also affect Sway, Hyprland, and KDE Plasma tiling under similar tile-move + maximize sequences.
