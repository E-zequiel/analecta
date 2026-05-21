#!/usr/bin/env bash
# Static checks + tests for Python sidecar and SvelteKit frontend.
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

# ── SvelteKit frontend ────────────────────────────────────────────────────────
cd "$REPO_ROOT"

echo "==> svelte-check"
mise exec -- pnpm --filter frontend check

echo "==> vite build"
mise exec -- pnpm --filter frontend build

echo "==> all checks passed"
