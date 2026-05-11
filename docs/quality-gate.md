# Quality Gate — Ruff + Basedpyright + pytest + Clippy

> Living document. Reflects active decisions on linting, type-checking, and testing.
> Update whenever `backend/pyproject.toml` or `src-tauri/` configuration changes.

---

## Running the gate

```bash
./scripts/check.sh   # from repo root
```

Steps in order:

1. `ruff format --check .` — Python formatting
2. `ruff check .` — Python linting
3. `basedpyright` — Python strict type-checking
4. `pytest -m "not integration"` — Python unit tests
5. `cargo fmt --check` — Rust formatting
6. `cargo clippy -- -D warnings` — Rust linting (warnings treated as errors)
7. `cargo test` — Rust unit tests

The script fails on the first step that does not pass (`set -euo pipefail`). Every
implementation block is considered complete **only after this script passes clean**.

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
| `UP` | pyupgrade | modernises syntax for Python 3.13 |
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

### Excluded modules

No modules are currently excluded from basedpyright. All source is in scope.

### Third-party stubs installed as dev dependencies

| Library | Stub package |
|---------|-------------|
| `beautifulsoup4` (`bs4`) | `types-beautifulsoup4` |

### Real errors found when expanding basedpyright scope

Removing the module exclusions surfaced 6 genuine type errors that had been silently
ignored. All fixed:

| File | Error | Fix applied |
|------|-------|-------------|
| `extraction/article.py` | `reportUnusedImport` — `trafilatura.settings` unused | Removed the import |
| `extraction/article.py` | `reportUnnecessaryComparison` — `meta is not None` always true per stubs | Simplified to `meta.title or ""` |
| `extraction/assets.py` | `reportMissingTypeArgument` — `re.Match` without type arg | Changed to `re.Match[str]` |
| `extraction/youtube.py` | `reportMissingTypeArgument` — `list` without type arg (×2) | Changed to `list[Any]` |
| `security/virustotal.py` | `reportMissingTypeArgument` — `dict` without type arg | Changed to `dict[str, Any]` |

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

## Rust (cargo fmt + clippy + test)

### Formatting

`cargo fmt --check` enforces the default `rustfmt` style. No custom `rustfmt.toml`
is used — the defaults are idiomatic and sufficient.

### Clippy

`cargo clippy -- -D warnings` runs Clippy in strict mode: every warning is treated
as a compile error. No per-crate `#![allow(...)]` suppressions are permitted except
where a Clippy lint is demonstrably wrong for this codebase.

### External binary stub

`tauri-build` validates at compile time that the glob declared in `bundle.resources`
(`tauri.conf.json`) matches at least one file. The sidecar onedir
(`src-tauri/binaries/analecta-sidecar/`) is produced by F1/F2 (PyInstaller
+ `scripts/build_sidecar.py`) and is gitignored.

`check.sh` creates a minimal placeholder directory if the real onedir is absent:

```
src-tauri/binaries/analecta-sidecar/
└── _internal/
    └── placeholder    (zero-byte file)
```

This satisfies the `binaries/analecta-sidecar/**/*` glob that `tauri-build` checks.
No target triple is needed in the directory name — the glob covers the entire tree.

- **Before F1**: the stub allows `cargo clippy` and `cargo test` to run.
- **After F1**: `build_sidecar.py` produces the real onedir; `check.sh` skips creation.
- **Repeated `check.sh` runs after F1**: the `if [[ ! -d ... ]]` guard is a no-op.

### Tests

`cargo test` runs with 0 Rust unit tests until the sidecar lifecycle logic (C4)
is implemented. The gate still passes — `cargo test` exits 0 with an empty suite.
