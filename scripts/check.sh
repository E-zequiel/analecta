#!/usr/bin/env bash
# Static checks + tests for the Python sidecar.
# Run from the repo root: ./scripts/check.sh
# Exit code is non-zero if any step fails.
set -euo pipefail

cd "$(dirname "$0")/../backend"

echo "==> ruff format --check"
mise exec -- uv run ruff format --check .

echo "==> ruff check"
mise exec -- uv run ruff check .

echo "==> basedpyright"
mise exec -- uv run basedpyright

echo "==> pytest (unit)"
mise exec -- uv run pytest -m "not integration"

echo "==> all checks passed"
