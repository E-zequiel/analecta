---
name: pytest
description: |
  Apply when writing, reviewing, or running Python tests in the Analecta backend.
  Use whenever: adding a new test file or fixture, writing tests for a new feature,
  debugging a failing test, checking test coverage, or auditing the test suite quality.
  The backend test suite lives in backend/tests/ and uses pytest + httpx + pytest-asyncio.
  Trigger proactively when any test code is written or modified.
---

# Analecta Backend Testing

## Working directory

All test commands run from `backend/`. Never run pytest from the repo root.

```bash
cd backend
mise exec -- uv run pytest                        # full suite
mise exec -- uv run pytest -m "not integration"   # unit tests only (same as CI)
mise exec -- uv run pytest tests/test_foo.py -v   # single file
```

## Quality gate

Before committing, run from the **repo root**:

```bash
./scripts/check.sh
```

This runs (in order): `ruff format --check`, `ruff check`, `basedpyright`, `pytest -m "not integration"`, `svelte-check`, `vite build`. All must pass clean.

## Integration marker

Tests that make real network calls must be marked:

```python
@pytest.mark.integration
async def test_fetch_real_url(client: httpx.AsyncClient) -> None:
    ...
```

CI excludes them with `-m "not integration"`. Run locally with:

```bash
mise exec -- uv run pytest -m integration
```

Never make real network calls from unmarked tests.

## Fixtures (defined in conftest.py — use these, don't reinvent them)

| Fixture | Type | What it provides |
|---------|------|-----------------|
| `tmp_vault` | `Path` | Temp directory as vault root (scoped to test) |
| `app_config` | `AppConfig` | `AppConfig` pointed at `tmp_vault` |
| `index` | `VaultIndex` | SQLite VaultIndex in `tmp_vault`; closed after test |
| `vault` | `VaultManager` | `VaultManager` pointed at `tmp_vault` |
| `client` | `httpx.AsyncClient` | Full FastAPI app via ASGI transport (all routers) |

## HTTP client pattern

Use `httpx.AsyncClient` with `ASGITransport`. Never use `starlette.testclient.TestClient`.

```python
# ✅ Correct — in-process transport, no real TCP sockets, works with async
async def test_create_entry(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/entries", json={...})
    assert response.status_code == 201

# ❌ Wrong — TestClient cannot handle streaming SSE responses and blocks the event loop
from starlette.testclient import TestClient
```

## SSE constraint

Never stream an infinite SSE response through the test client. `GET /api/v1/system/events` streams indefinitely — connecting to it in a test will hang forever.

```python
# ❌ Hangs the test suite
response = await client.get("/api/v1/system/events")

# ✅ Mock EventSourceResponse with a finite response in tests
mocker.patch("analecta.api.routes.system.EventSourceResponse", return_value=Response("data: ok\n\n"))
```

## Async tests

All async tests require the explicit mark (enforced by `asyncio_mode = strict`):

```python
@pytest.mark.asyncio
async def test_something(client: httpx.AsyncClient) -> None:
    ...
```

Fixtures that `yield` from async generators also need the mark:

```python
@pytest.fixture
@pytest.mark.asyncio
async def my_async_fixture() -> AsyncGenerator[Foo]:
    yield Foo()
```

## Mocking

Use `pytest-mock` (`mocker` fixture) exclusively. Never use `unittest.mock` context managers or decorators.

```python
# ✅
def test_extraction(mocker: pytest.MockerFixture) -> None:
    mocker.patch("analecta.extraction.article.httpx.AsyncClient.get", return_value=...)

# ❌
from unittest.mock import patch
@patch("analecta.extraction.article.httpx.AsyncClient.get")
def test_extraction(mock_get): ...
```

Patch the name as it is imported in the **target module**, not where it is defined.

## Coverage

Branch coverage is configured and runs automatically (`--cov-branch` in `pyproject.toml`). Do not downgrade to line-only coverage. The threshold is not enforced numerically but significant regressions should be investigated.

## Property-based testing

Use `hypothesis` for logical contracts and data validation edge cases, especially in extraction, markdown, and storage modules where input variety matters.

```python
from hypothesis import given, strategies as st

@given(st.text(min_size=1))
def test_slug_is_always_valid(title: str) -> None:
    slug = make_slug(title)
    assert slug.isascii()
```

## Configuration

All pytest, coverage, ruff, and basedpyright configuration lives in `backend/pyproject.toml`. Never create `.coveragerc`, `pytest.ini`, `setup.cfg`, or `tox.ini`.
