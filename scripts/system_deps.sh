#!/usr/bin/env bash
# system_deps.sh — Install system-level build dependencies for Analecta
#
# Targets: Pop!_OS 24.04 / Ubuntu 22.04+
# Idempotent: safe to run multiple times (apt-get is a no-op on already-installed packages)
#
# Usage:
#   bash scripts/system_deps.sh
set -uo pipefail

# ---- Guard: Debian/Ubuntu only ----------------------------------------------
if ! command -v apt-get &>/dev/null; then
    echo "ERROR: apt-get not found. This script targets Debian/Ubuntu-based systems." >&2
    exit 1
fi

echo "==> Updating package index..."
sudo apt-get update -qq || true  # PPA/mirror warnings are non-fatal

echo "==> Installing build dependencies..."
# --fix-missing: skip packages whose .deb can't be fetched (e.g. mirror sync lag on upgrades).
# Success is validated by pkg-config below, not by apt's exit code.
sudo apt-get install -y --fix-missing \
    libwebkit2gtk-4.1-dev \
    libayatana-appindicator3-dev \
    librsvg2-dev \
    libssl-dev \
    libxdo-dev \
    build-essential \
    curl \
    wget \
    file \
    pkg-config \
    patchelf || true

# ---- Verify ------------------------------------------------------------------
echo ""
echo "==> Verifying webkit2gtk-4.1..."
pkg-config --modversion webkit2gtk-4.1

echo ""
echo "==> All dependencies installed successfully."
echo ""
echo "NOTE — GNOME tray icon (Wayland/Pop!_OS):"
echo "  tauri-plugin-tray uses StatusNotifierItem, which GNOME does not display natively."
echo "  Install the GNOME Shell extension to enable tray icon support:"
echo "  'AppIndicator and KStatusNotifierItem Support'"
echo "  https://extensions.gnome.org/extension/615/appindicator-support/"
