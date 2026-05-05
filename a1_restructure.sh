#!/usr/bin/env zsh
# =============================================================================
# Block A1 — Repo restructure
# Branch: feat/hybrid-architecture
#
# What this does (git mv only — zero functional changes):
#   src/analecta/  →  backend/src/analecta/
#   tests/         →  backend/tests/
#   pyproject.toml →  backend/pyproject.toml
#   uv.lock        →  backend/uv.lock
#
# Also creates:
#   frontend/, src-tauri/, scripts/, .github/workflows/  (with .gitkeep)
#   package.json (root, Tauri scripts)
#   Extends .gitignore with Node/Rust/Tauri patterns
#
# Verification: cd backend && mise exec -- uv sync && mise exec -- uv run pytest -v
# Expected: 386 tests pass, zero functional changes.
# =============================================================================
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
cd "$REPO"

# ---- Safety checks ----------------------------------------------------------

BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [[ "$BRANCH" != "feat/hybrid-architecture" ]]; then
  print -u2 "ERROR: expected feat/hybrid-architecture, got '$BRANCH'"
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  print -u2 "ERROR: uncommitted changes detected. Commit or stash first."
  exit 1
fi

# ---- 1. Move Python project into backend/ -----------------------------------

mkdir -p backend
git mv src backend/src
git mv tests backend/tests
git mv pyproject.toml backend/pyproject.toml
git mv uv.lock backend/uv.lock

# .python-version is not git-tracked; move manually so mise/pyenv find it
[[ -f .python-version ]] && mv .python-version backend/.python-version

# ---- 2. Scaffold empty dirs (git doesn't track empty dirs) ------------------

mkdir -p frontend src-tauri scripts .github/workflows
touch frontend/.gitkeep src-tauri/.gitkeep scripts/.gitkeep .github/workflows/.gitkeep
git add frontend/.gitkeep src-tauri/.gitkeep scripts/.gitkeep .github/workflows/.gitkeep

# ---- 3. Root package.json ---------------------------------------------------

cat > package.json <<'EOF'
{
  "name": "analecta",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "tauri": "tauri",
    "dev": "tauri dev",
    "build": "tauri build"
  },
  "devDependencies": {
    "@tauri-apps/cli": "^2"
  }
}
EOF
git add package.json

# ---- 4. Extend .gitignore with Rust / Node / Tauri patterns -----------------

cat >> .gitignore <<'EOF'

# ========================
# Node
# ========================
node_modules/

# ========================
# Rust / Tauri
# ========================
src-tauri/target/
src-tauri/binaries/

# ========================
# SvelteKit
# ========================
frontend/build/
frontend/.svelte-kit/
EOF
git add .gitignore

# ---- Summary ----------------------------------------------------------------

print ""
print "=== git status ==="
git status --short
print ""
print "=== Staged diff stat ==="
git diff --cached --stat
print ""
print "All changes are staged but NOT committed."
print "Review above, then: git commit -m 'chore(repo): restructure into backend/ frontend/ src-tauri/ (A1)'"
print ""
print "=== Verification ==="
print "cd backend && mise exec -- uv sync && mise exec -- uv run pytest -v"
print "Expected: 386 tests pass."
