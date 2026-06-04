#!/usr/bin/env bash
# Static checks + tests for Python sidecar and SvelteKit/Electron frontend.
# Run from the repo root: ./scripts/check.sh
# Exit code is non-zero if any step fails.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ── Python sidecar ────────────────────────────────────────────────────────────
cd "$REPO_ROOT/backend"

echo "==> ruff format --check"
mise exec -- uv run ruff format --check . ../scripts/

echo "==> ruff check"
mise exec -- uv run ruff check . ../scripts/

echo "==> basedpyright"
mise exec -- uv run basedpyright
mise exec -- uv run basedpyright ../scripts/

echo "==> pytest (unit)"
mise exec -- uv run pytest -m "not integration"

# ── TypeScript / Svelte ───────────────────────────────────────────────────────
cd "$REPO_ROOT"

echo "==> svelte-kit sync (required before ESLint type-checked rules)"
mise exec -- pnpm --filter frontend exec svelte-kit sync

echo "==> prettier --check"
mise exec -- pnpm exec prettier --check "electron/**/*.ts" "frontend/src/**/*.{ts,svelte}"

echo "==> eslint"
mise exec -- pnpm exec eslint electron/main electron/preload frontend/src

echo "==> tsc --noEmit (electron)"
cd "$REPO_ROOT/electron" && mise exec -- pnpm exec tsc --noEmit
cd "$REPO_ROOT"

echo "==> svelte-check"
mise exec -- pnpm --filter frontend check --fail-on-warnings

echo "==> vite build"
mise exec -- pnpm --filter frontend build

echo "==> all checks passed"
