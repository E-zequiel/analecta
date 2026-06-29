# Contributing to Analecta

Analecta is primarily a personal project. Bug reports and small improvements are welcome. Before working on a significant change, open an issue first so we can discuss the approach — this avoids wasted effort on both sides.

By participating in this project, you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

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

> [!IMPORTANT]
> CI includes a dependency security scan that only runs on branches within this repository, not on fork pull requests. Fork PRs cannot pass the required CI checks and cannot be merged directly.

The project uses a maintainer-applies workflow:

1. **Open an issue** describing the change. Wait for maintainer sign-off before writing code — this avoids wasted effort on both sides.
2. **Develop your changes** on a local fork or clone. Run `mise exec -- ./scripts/check.sh` and confirm it passes with zero warnings.
3. **Share your work** via the issue thread: either a link to your fork branch or `git format-patch` output attached to the issue.
4. **The maintainer applies your commits** to a branch in this repository, preserving your authorship, and opens the PR from there. You appear as the commit author in the project history.

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
