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
mise exec -- uv run pytest -m "not integration" --ignore=tests/test_updater.py

# ── Rust shell ────────────────────────────────────────────────────────────────
cd "$REPO_ROOT/src-tauri"

# tauri-build validates the resources glob `binaries/analecta-sidecar/**/*` at
# compile time. The real onedir is produced by scripts/build_sidecar.py (F2).
# If it hasn't been built yet, create a minimal placeholder so clippy/test can
# run. The placeholder is never committed (binaries/ is gitignored) and is
# ignored once the real onedir is present.
SIDECAR_DIR="$REPO_ROOT/src-tauri/binaries/analecta-sidecar"
if [[ ! -d "$SIDECAR_DIR" ]]; then
    mkdir -p "$SIDECAR_DIR/_internal"
    touch "$SIDECAR_DIR/_internal/placeholder"
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
