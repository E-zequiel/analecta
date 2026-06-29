#!/usr/bin/env bash
# Static checks + tests for Python sidecar and SvelteKit/Electron frontend.
# Run from the repo root: ./scripts/check.sh [backend|frontend]
# With no argument, runs all checks. Exit code is non-zero if any step fails.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

run_backend() {
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
}

run_frontend() {
  cd "$REPO_ROOT"

  echo "==> svelte-kit sync (required before ESLint type-checked rules)"
  mise exec -- pnpm --filter frontend exec svelte-kit sync

  echo "==> prettier --check"
  mise exec -- pnpm exec prettier --check "electron/**/*.ts" "frontend/src/**/*.{ts,svelte}" "frontend/scripts/**/*.mjs"

  echo "==> eslint"
  mise exec -- pnpm exec eslint electron/main electron/preload frontend/src frontend/scripts

  echo "==> tsc --noEmit (electron)"
  cd "$REPO_ROOT/electron" && mise exec -- pnpm exec tsc --noEmit
  cd "$REPO_ROOT"

  echo "==> svelte-check"
  mise exec -- pnpm --filter frontend check --fail-on-warnings

  echo "==> vite build"
  mise exec -- pnpm --filter frontend build
}

case "${1:-all}" in
  backend)
    run_backend
    ;;
  frontend)
    run_frontend
    ;;
  all)
    run_backend
    run_frontend
    ;;
  *)
    echo "Usage: $0 [backend|frontend]" >&2
    exit 1
    ;;
esac

echo "==> all checks passed"
