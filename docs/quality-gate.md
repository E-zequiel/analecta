# Quality Gate — Analecta

> Living document. Reflects active decisions on formatting, linting, type-checking, and testing.
> Update whenever `backend/pyproject.toml`, `eslint.config.js`, or `.prettierrc` configuration changes.

---

## Running the gate

```bash
./scripts/check.sh   # from repo root
```

Steps in order:

**Python (backend/):**

1. `ruff format --check .` — Python formatting
2. `ruff check .` — Python linting
3. `basedpyright` — Python strict type-checking
4. `pytest -m "not integration"` — Python unit tests

**TypeScript / Svelte:**

5. `pnpm --filter frontend exec svelte-kit sync` — generates `.svelte-kit/tsconfig.json` (required before ESLint type-checked rules)
6. `pnpm exec prettier --check "electron/**/*.ts" "frontend/src/**/*.{ts,svelte}"` — formatting
7. `pnpm exec eslint electron/main electron/preload frontend/src` — linting
8. `cd electron && pnpm exec tsc --noEmit` — Electron type-checking
9. `pnpm --filter frontend check --fail-on-warnings` — SvelteKit type-checking (`svelte-check`)
10. `pnpm --filter frontend build` — Vite production build

The script fails on the first step that does not pass (`set -euo pipefail`). Every
implementation block is considered complete **only after this script passes clean**.

---

## Accepted warnings

### svelte-check

`svelte-check` is run with `--fail-on-warnings`. Exactly one warning is accepted as a
tooling limitation:

| File | Warning code | Reason |
|------|-------------|--------|
| `frontend/src/lib/components/MarkdownEditor.svelte` | `state_referenced_locally` | CodeMirror requires capturing the initial prop value inside `onMount`; the compiler still emits the warning because the variable is declared at module scope before the assignment. Unfixable without breaking CodeMirror initialization. |

Any warning outside this exception must be investigated and resolved before considering
a block complete.

### vite build: accepted chunk size hint

`vite build` may emit a `(!) Some chunks are larger than 500 kB` hint for the editor
route (`/editor/[id]`). The chunk contains the full CodeMirror 6 ecosystem
(`@codemirror/*`, `@lezer/*`, `@uiw/codemirror-theme-tokyo-night`) which is a monolith
by design. It loads only on navigation to the editor, never on initial app launch.
Network latency is irrelevant in an Electron desktop app. `build.chunkSizeWarningLimit`
is set to `600` in `vite.config.ts` to suppress this hint.

---

## Ruff

### Active rule sets

| Prefix | Origin | Purpose |
|--------|--------|---------|
| `E` | pycodestyle | PEP 8 style |
| `F` | Pyflakes | basic errors (unused imports, etc.) |
| `B` | flake8-bugbear | subtle anti-patterns |
| `I` | isort | import ordering |
| `D` | pydocstyle | docstrings (Google convention) |
| `UP` | pyupgrade | modernises syntax for Python 3.14 |
| `RUF` | Ruff-native | rules with no equivalent in other linters |
| `ASYNC` | flake8-async | anti-patterns in async code |
| `PT` | flake8-pytest-style | consistency in test files |

### Global ignores

| Rule | Reason |
|------|--------|
| `B008` | FastAPI: `Depends()` in argument defaults is the intended pattern |
| `D100` | Module-level docstrings not required |
| `D104` | Package-level docstrings not required |
| `D105` | Magic method docstrings not required |
| `D107` | `__init__` args documented in class docstring (Google style) |

### Per-file ignores

| Pattern | Ignored rules | Reason |
|---------|--------------|--------|
| `tests/**` | `D` (all) | Test files do not need docstrings |

### ASYNC110 in integration tests

`tests/test_api_b6.py` polls `uvicorn.Server.started` with a `while/sleep` loop.
`ASYNC110` suggests using `asyncio.Event` instead, but `server.started` is an internal
uvicorn attribute that cannot be replaced with a custom Event. The line is suppressed
with `# noqa: ASYNC110`.

---

## Basedpyright

### Mode

`typeCheckingMode = "strict"` — the highest level. Catches type errors that `basic`
and `standard` miss.

### Report settings

| Setting | Value | Reason |
|---------|-------|--------|
| `reportUnusedFunction` | `"none"` | False positive on functions registered as FastAPI route handlers via decorators |
| `reportMissingTypeStubs` | `"none"` | Several dependencies ship no stubs (`trafilatura`, `markdownify`, `youtube-transcript-api`) |
| `reportUnknownMemberType` | `"none"` | Avoids noise from calls into stub-less libraries |
| `reportUnknownArgumentType` | `"none"` | Same reason |
| `reportUnknownVariableType` | `"none"` | Same reason |
| `reportUnknownParameterType` | `"none"` | Same reason |

### Why relax `reportUnknown*` instead of excluding modules

An earlier configuration excluded `extraction/`, `markdown/`, and `security/` entirely
from basedpyright, citing the lack of stubs in their third-party dependencies. That
approach had a critical flaw: excluding a module also silences type errors in **our own
code** inside it — covering ~30% of the business logic.

The correct approach is to keep those modules in scope and relax the `Unknown`-related
reports that originate from untyped third parties. Basedpyright continues to check
control flow, our own types, function signatures, and return types.

### Third-party stubs installed as dev dependencies

| Library | Stub package |
|---------|-------------|
| `beautifulsoup4` (`bs4`) | `types-beautifulsoup4` |

---

## pytest

### Relevant configuration

```toml
asyncio_mode = "strict"
asyncio_default_fixture_loop_scope = "function"
```

`asyncio_mode = "strict"` requires `@pytest.mark.asyncio` (or an async fixture) on
every async test. Prevents async tests from running silently as synchronous.

`asyncio_default_fixture_loop_scope = "function"` pins the event loop scope explicitly,
avoiding the pytest-asyncio deprecation warning about the implicit default changing in
future versions.

### Markers

| Marker | Usage |
|--------|-------|
| `integration` | Tests that make real network calls. Exclude locally with `-m "not integration"`. |

### Filtered warnings

| Warning | Reason |
|---------|--------|
| `DeprecationWarning` from `websockets` | `uvicorn 0.46` uses the `websockets.legacy` API deprecated in websockets 14+. Upstream issue, not fixable from this repo. |
| `DeprecationWarning` from `uvicorn` | Same root cause: the warning is attributed to the calling module (`uvicorn/protocols/websockets/websockets_impl.py`) due to the `stacklevel` set by websockets. |

---

## Coverage

`--cov=analecta --cov-branch --cov-report=term-missing` runs on every pytest invocation.

Modules with intentionally low coverage:

| Module | Coverage | Reason |
|--------|----------|--------|
| `server.py` | ~0% | uvicorn entrypoint; tested via integration, not unit |
| `__main__.py` | 0% | Three-line shim; no logic to test |
| `config.py` | ~60% | `load_config` from file and `save_config` lack I/O tests |

---

## Prettier

Config at `.prettierrc` (repo root):

| Option | Value | Reason |
|--------|-------|--------|
| `useTabs` | `true` | Matches existing frontend convention |
| `singleQuote` | `true` | Consistent with TypeScript ecosystem preference |
| `semi` | `true` | Explicit semicolons |
| `trailingComma` | `"es5"` | Trailing commas where valid in ES5 |
| `printWidth` | `100` | Wider than default 80; appropriate for desktop-only code |
| `plugins` | `["prettier-plugin-svelte"]` | Svelte parser support |

`.prettierignore` excludes: `node_modules`, `dist`, `build`, `.svelte-kit`, `binaries`,
`scripts`, `*.md`, `pnpm-lock.yaml`.

`pnpm exec prettier --write` is the fix command. `--check` is what `check.sh` runs.

---

## ESLint

Config at `eslint.config.js` (repo root, ESM flat config). Three rule tiers:

### Electron TypeScript (`electron/**/*.ts`)

Uses `tseslint.configs.recommendedTypeChecked` with `electron/tsconfig.json` as the
type-check project. Key rules enabled by this tier:

- `@typescript-eslint/no-floating-promises` — catches unhandled Promises (e.g., unvoided `goto()` calls)
- `@typescript-eslint/no-unsafe-*` — flags dynamic type escapes (`any` coercions, unsafe calls)
- `@typescript-eslint/require-await` — flags async functions with no await

### Frontend TypeScript (`frontend/src/**/*.ts`)

Same `recommendedTypeChecked` tier with `frontend/tsconfig.json`. Additional rule:

- `@typescript-eslint/no-unused-vars`: `['error', { argsIgnorePattern: '^_' }]` — allows intentionally unused parameters prefixed with `_`

### Svelte components (`frontend/src/**/*.svelte`)

Uses `tseslint.configs.recommended` (not type-checked — svelte-check handles that) plus
`eslint-plugin-svelte` `flat/recommended`. Key decisions:

- `svelte/no-navigation-without-resolve`: `['error', { ignoreGoto: true, ignoreLinks: true }]` — the rule targets `onNavigate` hooks that require a `complete()` callback; regular `goto()` calls in event handlers do not need it
- `svelte/prefer-svelte-reactivity` — fires when `new Map()` or `new Set()` is used in a Svelte file; suppress with `eslint-disable-next-line` for local algorithmic uses (e.g., inside store updater callbacks or pure functions); convert to `SvelteMap`/`SvelteSet` when the value is reactive component state
- `@typescript-eslint/no-unused-vars`: same `argsIgnorePattern: '^_'` as frontend TS

`eslint-config-prettier` is the last entry — disables all formatting rules that conflict
with Prettier.

### Suppression policy

Use `// eslint-disable-next-line <rule> -- <reason>` (single-line, with reason comment).
Never use file-level `/* eslint-disable */` blocks. Document the reason inline.

---

## TypeScript (Electron — `tsc --noEmit`)

`cd electron && pnpm exec tsc --noEmit` checks the Electron main/preload TypeScript
against `electron/tsconfig.json`. ESLint type-checked rules require this tsconfig to
be present and valid.

The frontend TypeScript is type-checked by `svelte-check` (step 9), which internally
runs `tsc` with the SvelteKit-generated `.svelte-kit/tsconfig.json`.
