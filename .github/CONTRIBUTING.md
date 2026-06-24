# Contributing to Analecta

Analecta is primarily a personal project. Bug reports and small improvements are welcome. Before working on a significant change, open an issue first so we can discuss the approach — this avoids wasted effort on both sides.

## Prerequisites

A single tool manages the entire toolchain:

- **[mise](https://mise.jdx.dev/)** — installs Python 3.14, Node.js, and pnpm at the exact versions declared in `.mise.toml`.

Install mise following the [official instructions](https://mise.jdx.dev/getting-started.html), then run:

```sh
mise install
```

No other global installations are required.

## Getting started

```sh
git clone https://github.com/E-zequiel/analecta.git
cd analecta

# Install toolchain (Python 3.14, Node, pnpm)
mise install

# Install backend dependencies
cd backend && mise exec -- uv sync && cd ..

# Install frontend and Electron dependencies
mise exec -- pnpm install
```

## Running in development

**Backend sidecar only** (API server, prints its port to stdout):

```sh
cd backend && mise exec -- uv run python -m analecta
```

**Full application** (Electron shell + SvelteKit hot reload + sidecar):

```sh
mise exec -- pnpm electron:dev
```

## Building from source

> [!IMPORTANT]
> The sidecar build **must** be run from the repository root. Running it from `backend/` exits silently with a stale binary.

```sh
# Build the Python sidecar (PyInstaller → binaries/)
mise exec -- uv run python scripts/build_sidecar.py

# Full distribution build (.deb / .rpm / .AppImage → dist/)
mise exec -- pnpm dist
```

## Running tests

```sh
cd backend && mise exec -- uv run pytest -v
```

## Quality gate

All contributions must pass the quality gate with **zero warnings** before opening a PR. CI enforces the same check:

```sh
mise exec -- ./scripts/check.sh
```

The gate covers Python (ruff, basedpyright, pytest) and TypeScript/Svelte (ESLint, svelte-check, prettier).

### Test requirement

Every change to `backend/src/analecta/**` must include tests in `backend/tests/` in the same commit. Zero coverage on new backend code blocks merging.

TypeScript, Svelte, and Electron code are covered by manual QA only; no automated frontend tests are required.

## Commit conventions

Commits follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(area): short description
fix(area): short description
docs: short description
refactor(area): short description
test(area): short description
chore: short description
```

Use the imperative mood. Keep the subject line under 72 characters.

## Submitting a pull request

1. Fork the repository and create a branch from `main`.
2. Make your change. Run `mise exec -- ./scripts/check.sh` and confirm it passes with zero warnings.
3. Open a pull request against `main` with a clear description of what changes and why.

Response times are best-effort for a solo-maintained project.

## Hard constraints

These apply to all contributions — violations will block merging:

| Rule | Detail |
|------|--------|
| **pnpm only** | Never use `npm` or `yarn`. |
| **Exact version pins** | No version ranges (`^`, `~`) in `package.json`. `.npmrc` enforces `save-exact=true`. |
| **No `requests`** | Use `httpx2` for all HTTP I/O in the Python sidecar. |
| **SQLite only** | No PostgreSQL, no ORM, no Alembic. Schema changes go in a numbered migration file (`backend/src/analecta/migrations/NNN_description.sql`). |
| **No Docker** | The sidecar is packaged with PyInstaller; the dev workflow uses `mise`. |
| **Linux only** | Analecta targets Linux x86\_64 exclusively. macOS and Windows are not supported. |

## Dependency changes

Adding or upgrading dependencies requires following the verification protocol in [`docs/dependency-verification.md`](../docs/dependency-verification.md). This applies to both Python and Node packages.
