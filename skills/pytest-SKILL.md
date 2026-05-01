# Python Testing & TDD Architect

## Description
Expert guidelines for designing, refactoring, and executing Python tests using pytest. This skill enforces modern architectural standards, strict environment isolation, and advanced quality assurance practices.

## Environment & Execution
- All test executions and dependency management must be performed using `uv` (e.g., `mise exec -- uv run pytest`).
- Run `mise exec -- uv run ruff check src/ tests/` before committing. All checks must pass with zero errors.
- Ensure all configurations (pytest, coverage, formatting, marks) are strictly centralized in `pyproject.toml`.
- Never create, modify, or suggest legacy configuration files such as `.coveragerc`, `pytest.ini`, or `setup.cfg`.

## Architectural Rules for Testing

### 1. Mocking & Dependency Injection
- **Referential Resolution:** Always mock the object in the exact namespace where it is ultimately used (the target module), not in the module where it is defined.
- **Fixture Usage:** Exclusively use the `pytest-mock` library (via the `mocker` fixture) for patching. Do not use `unittest.mock` context managers or decorators.
- **State Management:** Use the `yield` statement in fixtures to clearly separate setup from teardown, delegating state cleanup to the framework.

### 2. Security & Network Isolation
- **Zero-Trust Testing:** Implement `pytest-socket` to actively block all system-level socket creations during test execution. 
- Ensure no test can inadvertently resolve DNS or open TCP connections to external services.

### 3. Coverage & Quality Gates
- **Branch Coverage:** Line coverage is insufficient. Always measure branch coverage by enforcing `--cov-branch` in commands and configuration.
- **Property-Based Testing:** When evaluating logical contracts, edge cases, or data validation, implement `hypothesis` to generate robust, typed input vectors instead of relying solely on static tuples in `@pytest.mark.parametrize`.

### 4. Asynchronous Testing
- **Strict Mode:** Configure `pytest-asyncio` with `asyncio_mode = strict` in `pyproject.toml`. 
- All async tests must be explicitly marked with `@pytest.mark.asyncio` to prevent implicit behaviors from masking unawaited coroutines.

## Workflow Directives
When instructed to write or audit tests:
1. Analyze the target code to map out dependencies, external calls, and state mutations.
2. Verify or generate the corresponding `[tool.pytest.ini_options]` and `[tool.coverage.run]` blocks in `pyproject.toml`.
3. Write the tests following TDD principles: define the expected API contract and failure states first.
4. Apply the `mocker` fixture to isolate the component under test.
5. Execute the suite via `mise exec -- uv run pytest` and verify branch coverage and network isolation.
