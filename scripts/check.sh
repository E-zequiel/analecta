#!/usr/bin/env bash
# Static checks + tests for Python sidecar, Rust shell, and SvelteKit frontend.
# Run from the repo root: ./scripts/check.sh
# Exit code is non-zero if any step fails.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ── Python sidecar ────────────────────────────────────────────────────────────
cd "$REPO_ROOT/backend"

echo "==> ruff format --check"
mise exec -- uv run ruff format --check .

echo "==> ruff check"
mise exec -- uv run ruff check .

echo "==> basedpyright"
mise exec -- uv run basedpyright

echo "==> pytest (unit)"
mise exec -- uv run pytest -m "not integration"

# ── Rust shell ────────────────────────────────────────────────────────────────
cd "$REPO_ROOT/src-tauri"

# tauri-build validates externalBin existence at compile time; the real binary
# is produced by F1/F2 (PyInstaller + build_sidecar.py). Create a zero-byte stub
# so clippy/test can run without a full sidecar build. Uses the same host-tuple
# logic as build_sidecar.py so the path always matches. The binaries/ dir is
# gitignored, so the stub is never committed and is overwritten by F2 when built.
TARGET_TRIPLE=$(mise exec -- rustc --print host-tuple)
SIDECAR="$REPO_ROOT/src-tauri/binaries/analecta-sidecar-$TARGET_TRIPLE"
if [[ ! -f "$SIDECAR" ]]; then
    mkdir -p "$(dirname "$SIDECAR")"
    touch "$SIDECAR"
fi

echo "==> cargo fmt --check"
mise exec -- cargo fmt --check

echo "==> cargo clippy"
mise exec -- cargo clippy -- -D warnings

echo "==> cargo test"
mise exec -- cargo test

# ── SvelteKit frontend ────────────────────────────────────────────────────────
cd "$REPO_ROOT"

echo "==> svelte-check"
mise exec -- pnpm --filter frontend check

echo "==> vite build"
mise exec -- pnpm --filter frontend build

echo "==> all checks passed"
