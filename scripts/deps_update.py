#!/usr/bin/env python3
"""Age-gated dependency updater for Analecta.

For each ecosystem (Python/uv, Node/pnpm):
  1. Detect packages with available updates.
  2. Query the upstream registry for the release date of the new version.
  3. Skip packages released less than COOLDOWN_DAYS ago.
  4. Apply selective updates for packages that pass the gate.
  5. With --verify: check the whole batch against check.sh; on failure,
     replay the batch one package at a time to isolate and exclude only the
     package(s) that actually broke it, instead of discarding the batch.

A hard error in one Node workspace (or in the Python ecosystem) does not
prevent a PR for the others — it is reported in the PR body next to that
ecosystem's section. The exit code only signals failure when nothing
succeeded anywhere in the run.

Usage (from repo root):
    python scripts/deps_update.py [--cooldown DAYS] [--pr-body-file PATH] [--verify]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).parent.parent
COOLDOWN_DAYS = 10
_REGISTRY_TIMEOUT = 15
_WORKSPACE_DIR = {
    "frontend": "frontend",
    "analecta-electron": "electron",
    "analecta": ".",
}

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

type Updated = tuple[str, str, str, datetime]  # (name, old, new, release_dt)
type Skipped = tuple[str, str, datetime]  # (name, new, release_dt)
type Blocked = tuple[str, str, str]  # (name, new, reason)

# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------


def _fetch_json(
    url: str, *, headers: dict[str, str] | None = None
) -> dict[str, Any] | None:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=_REGISTRY_TIMEOUT) as resp:  # pyright: ignore[reportAny]
            return cast(dict[str, Any], json.loads(resp.read()))  # pyright: ignore[reportAny]
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        print(f"    [warn] registry error for {url}: {exc}")
        return None


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _age_ok(release_dt: datetime, cooldown: int) -> bool:
    return datetime.now(UTC) - release_dt >= timedelta(days=cooldown)


def _parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _run_check(repo_root: Path, scope: str) -> bool:
    """Run check.sh for the given scope ('backend' or 'frontend') and stream output."""
    print(f"\n==> Verifying with check.sh {scope} …")
    result = subprocess.run(["bash", "scripts/check.sh", scope], cwd=repo_root)
    return result.returncode == 0


def _record_error(errors: list[str], msg: str) -> None:
    """Emit a GitHub Actions ::error:: annotation and record msg for the PR body.

    The ::error:: print is newline-collapsed the same way _pr_body sanitizes
    msg before committing it — an embedded newline in subprocess-derived
    text would otherwise let a later line in the same message get parsed as
    its own workflow command (e.g. ::stop-commands::) by the Actions runner.
    A much higher limit than _sanitize_reason's PR-body default (200, sized
    for a markdown table cell): this print has no such width constraint, and
    the CI log annotation was never truncated before this fix — only the
    newline-collapsing is the point here.
    """
    print(f"::error::{_sanitize_reason(msg, limit=2000)}")
    errors.append(msg)


def _record_registry_health(
    errors: list[str],
    *,
    fetch_attempted: int,
    fetch_ok: int,
    candidates: int,
    date_unknown: int,
    registry_label: str,
) -> None:
    """Escalate a fully-failed registry check to a run-level error.

    Shared by update_python/update_node: "every fetch failed" (the registry
    was unreachable) and "every release date was unusable" (the registry
    answered, but cooldown couldn't be evaluated for anything) both leave
    every candidate unchecked, but are distinct failures worth telling apart
    in the PR body rather than collapsing into one generic message.
    """
    if fetch_attempted > 0 and fetch_ok == 0:
        _record_error(
            errors,
            f"All {registry_label} registry fetches failed — no packages could"
            " be checked",
        )
    elif candidates > 0 and date_unknown == candidates:
        _record_error(
            errors,
            f"All {registry_label} release-date lookups failed for packages"
            " with an available update — cooldown could not be evaluated",
        )


def _restore_snapshot(snapshot: dict[Path, bytes]) -> None:
    """Write every path in *snapshot* back to its captured bytes."""
    for path, data in snapshot.items():
        _ = path.write_bytes(data)


def _guard[T](
    label: str,
    errors: list[str],
    snapshot: dict[Path, bytes],
    fn: Callable[[], T],
    *,
    on_restore: Callable[[], None] | None = None,
) -> T | None:
    """Run fn(); on any exception (or Ctrl+C), restore *snapshot* and record the crash.

    Centralizes what main()'s crash handlers must each do — restore state
    before recording the error, not after or not at all — so a new call
    site can't independently forget the restore half of that pair the way
    three of the four hand-written handlers this replaces once did. Every
    call site always has a snapshot to restore (captured unconditionally in
    main(), regardless of --verify) — *snapshot* is not Optional here.

    The restore itself can fail too (e.g. the same disk-full condition that
    triggered fn()'s crash also blocks the write-back) — that's caught and
    recorded rather than left to propagate uncaught out of _guard, which
    would otherwise abort the whole run before the crash it was trying to
    report ever reaches the PR body.

    KeyboardInterrupt is caught alongside Exception so a manual Ctrl+C mid-fn
    still triggers the same restore-and-record path instead of leaving
    mutated state (e.g. one already-applied package bump) unrecorded on
    disk. It is re-raised after cleanup so the process still stops, rather
    than _guard silently swallowing the interrupt and continuing on to the
    next workspace.

    *on_restore*, if given, runs after the snapshot restore and the crash
    message are recorded (preserving that message order) — e.g. a
    Node-domain node_modules resync, so a future Node call site can't
    independently forget that follow-up the same way the restore itself
    was once forgotten at 3 of the original 4 crash sites. Exceptions it
    raises are caught and recorded the same way fn()'s are, so a broken
    hook can't escape _guard uncaught either — reproducing this exact bug
    class one level up would defeat the point of moving it in here.

    Returns:
        fn()'s result, or None if it raised (KeyboardInterrupt re-raises
        after cleanup instead of returning).
    """
    try:
        return fn()
    except (Exception, KeyboardInterrupt) as exc:
        try:
            _restore_snapshot(snapshot)
        except Exception as restore_exc:
            _record_error(
                errors, f"{label} restore failed unexpectedly — {restore_exc}"
            )
        _record_error(errors, f"{label} crashed unexpectedly — {exc}")
        if on_restore is not None:
            try:
                on_restore()
            except Exception as hook_exc:
                _record_error(
                    errors, f"{label} restore hook crashed unexpectedly — {hook_exc}"
                )
        if isinstance(exc, KeyboardInterrupt):
            raise
        return None


def _sanitize_reason(text: str, limit: int = 200) -> str:
    """Make subprocess-derived text safe to interpolate into the committed PR body.

    A blocked package's reason can carry raw stderr/stdout from a package's
    own install step (via uv/pnpm) — untruncated, that text ends up verbatim
    in pr-body.md, which is committed and handed to `gh pr create` unreviewed.
    Collapsing whitespace and stripping markdown-structural characters keeps
    it from breaking out of its single-line context or forging PR body
    structure (headings, tables, comments) that a human reads before merging.
    """
    flat = " ".join(text.split())
    flat = flat.replace("`", "'").replace("|", "/")
    if len(flat) > limit:
        flat = flat[: limit - 1].rstrip() + "…"
    return flat


# ---------------------------------------------------------------------------
# Python / uv
# ---------------------------------------------------------------------------


def _pkg_name(dep: str) -> str:
    """Extract bare package name from a PEP 508 dependency string."""
    m = re.match(r"^([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)", dep.strip())
    return m.group(1) if m else ""


def _pypi_release_date(
    _name: str, version: str, all_releases: dict[str, Any]
) -> datetime | None:
    files = cast(list[dict[str, Any]], all_releases.get(version, []))
    times: list[str] = [
        cast(str, f.get("upload_time_iso_8601") or f.get("upload_time"))
        for f in files
        if f.get("upload_time_iso_8601") or f.get("upload_time")
    ]
    if not times:
        return None
    try:
        return _parse_iso(min(times))
    except ValueError, TypeError:
        return None


def _apply_python_package(name: str, backend: Path) -> tuple[bool, str]:
    """Apply a single Python package bump via `uv lock --upgrade-package`."""
    result = _run(["uv", "lock", "--upgrade-package", name], cwd=backend)
    if result.returncode != 0:
        return False, f"uv lock failed — {result.stderr.strip()}"
    return True, ""


def update_python(
    cooldown: int, errors: list[str]
) -> tuple[list[Updated], list[Skipped]]:
    """Check and apply Python dependency updates via uv.

    *errors* is a caller-owned list mutated in place rather than built
    locally and returned — a locally-built list would be lost in its
    entirety if an unhandled exception aborted the loop below partway
    through, discarding every error already recorded for packages processed
    before the one that crashed. The caller's list still holds them.
    """
    print("\n=== Python (uv) ===")
    backend = REPO_ROOT / "backend"

    with (backend / "pyproject.toml").open("rb") as f:
        pyproject = tomllib.load(f)

    project_section = cast(dict[str, Any], pyproject.get("project", {}))
    direct: list[str] = list(cast(list[str], project_section.get("dependencies", [])))
    dep_groups = cast(dict[str, list[Any]], pyproject.get("dependency-groups", {}))
    for group in dep_groups.values():
        direct.extend(d for d in group if isinstance(d, str))

    names = sorted({_pkg_name(d) for d in direct if _pkg_name(d)})

    with (backend / "uv.lock").open("rb") as f:
        lock = tomllib.load(f)
    packages = cast(list[dict[str, Any]], lock.get("package", []))
    current_versions: dict[str, str] = {
        cast(str, p["name"]): cast(str, p["version"]) for p in packages
    }

    updated: list[Updated] = []
    skipped: list[Skipped] = []
    fetch_attempted = 0
    fetch_ok = 0
    candidates = 0
    date_unknown = 0

    for name in names:
        fetch_attempted += 1
        data = _fetch_json(f"https://pypi.org/pypi/{name}/json")
        if data is None:
            continue
        fetch_ok += 1
        info = cast(dict[str, Any], data.get("info", {}))
        latest: str = cast(str, info.get("version", ""))
        current = current_versions.get(name, "")
        if not latest or latest == current:
            continue
        print(f"  {name}: {current} -> {latest}")
        candidates += 1

        releases = cast(dict[str, Any], data.get("releases", {}))
        release_dt = _pypi_release_date(name, latest, releases)
        if release_dt is None:
            print("    [warn] release date unavailable, skipping")
            date_unknown += 1
            continue
        if not _age_ok(release_dt, cooldown):
            days_old = (datetime.now(UTC) - release_dt).days
            print(f"    [skip] {days_old}d old (cooldown: {cooldown}d)")
            skipped.append((name, latest, release_dt))
            continue

        ok, err = _apply_python_package(name, backend)
        if not ok:
            _record_error(errors, f"{name}: {err}")
        else:
            print("    [ok] updated")
            updated.append((name, current, latest, release_dt))

    _record_registry_health(
        errors,
        fetch_attempted=fetch_attempted,
        fetch_ok=fetch_ok,
        candidates=candidates,
        date_unknown=date_unknown,
        registry_label="PyPI",
    )

    return updated, skipped


def _verify_python(
    backend: Path,
    uv_lock_path: Path,
    snapshot: dict[Path, bytes],
    batch: list[Updated],
    survivors: list[Updated],
    blocked: list[Blocked],
) -> None:
    """Verify a batch of Python updates with check.sh backend; bisect on failure.

    *survivors* and *blocked* are caller-owned lists mutated in place, not
    built locally and returned — a crash partway through bisection (caught
    by _guard) must not discard packages already reconfirmed, or already
    ruled out, before the crash point. Same reasoning as update_python's
    caller-owned *errors* list.

    *snapshot* is the same dict object _guard restores from on a crash, not
    a private copy — this function updates snapshot[uv_lock_path] in place
    to the just-applied bytes after every confirmed survivor. Without this,
    a crash later in the loop would still make _guard restore all the way
    back to the pre-batch pristine bytes, silently wiping the on-disk change
    for every survivor already confirmed before the crash — while
    *survivors* (deliberately preserved through the crash) keeps reporting
    them as applied, so the committed uv.lock and the PR body/CHANGELOG
    would disagree about what actually changed.
    """
    if _run_check(REPO_ROOT, "backend"):
        survivors.extend(batch)
        return

    print(
        "::warning::check.sh backend failed on the full batch"
        " — isolating the offending package(s)"
    )
    pristine = dict(snapshot)
    _restore_snapshot(pristine)

    for name, old, new, release_dt in batch:
        step_snap = uv_lock_path.read_bytes()
        ok, reason = _apply_python_package(name, backend)
        if ok:
            ok = _run_check(REPO_ROOT, "backend")
            reason = "check.sh backend failed"
        if ok:
            print(f"    [ok] {name}: confirmed in isolation")
            survivors.append((name, old, new, release_dt))
            snapshot[uv_lock_path] = uv_lock_path.read_bytes()
        else:
            _restore_snapshot({uv_lock_path: step_snap})
            print(f"::warning::{name}: blocked — {reason}")
            blocked.append((name, new, _sanitize_reason(reason)))


# ---------------------------------------------------------------------------
# Node / pnpm
# ---------------------------------------------------------------------------


def _parse_pnpm_outdated(raw: str) -> dict[str, dict[str, Any]]:
    """Parse pnpm outdated --json output (flat or workspace-nested)."""
    data = cast(dict[str, Any], json.loads(raw))
    if not data:
        return {}
    first = next(iter(data.values()), None)
    # Flat format: first value has "current" / "wanted" keys directly
    if isinstance(first, dict) and ("current" in first or "wanted" in first):
        return cast(dict[str, dict[str, Any]], data)
    # Workspace-nested: {"frontend": {"pkg": {...}}}
    merged: dict[str, dict[str, Any]] = {}
    for ws_pkgs in data.values():
        if isinstance(ws_pkgs, dict):
            merged.update(cast(dict[str, dict[str, Any]], ws_pkgs))
    return merged


def _npm_registry_data(
    name: str, cache: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any] | None:
    """Fetch npm registry metadata for name, optionally through a shared cache.

    Only successful responses are cached: a transient registry failure must
    stay retryable on the next lookup (e.g. the same package checked again
    in another workspace), not get pinned as a permanent miss.
    """
    if cache is not None and name in cache:
        return cache[name]
    encoded = name.replace("/", "%2F")
    data = _fetch_json(f"https://registry.npmjs.org/{encoded}")
    if data is not None and cache is not None:
        cache[name] = data
    return data


def _npm_release_date(data: dict[str, Any], version: str) -> datetime | None:
    time_dict = cast(dict[str, str], data.get("time", {}))
    ts = time_dict.get(version)
    if not ts:
        return None
    try:
        return _parse_iso(ts)
    except ValueError, TypeError:
        return None


_DEP_KEY_RE = re.compile(
    r'"(?:dependencies|devDependencies|peerDependencies|optionalDependencies'
    r'|bundledDependencies|bundleDependencies)"\s*:'
)


def _dependency_block_spans(text: str) -> list[tuple[int, int]]:
    r"""Find the [start, end) span of each real dependency-type object's body.

    Only the six standard npm dependency-type keys are recognized as block
    openers — not any `"\w*[Dd]ependencies"`-shaped string, which would also
    match a `scripts` entry like `"checkDependencies"` or a plain string
    value such as a `"keywords"` array containing `"dependencies"`.

    Each span is found via string/escape-aware brace-depth counting from the
    key's own `{`, not just "from here to the next occurrence of anything" —
    so a match is only ever accepted from inside a block that is actually
    one of those six keys' object, never from unrelated text between or
    after them.
    """
    spans: list[tuple[int, int]] = []
    for key_match in _DEP_KEY_RE.finditer(text):
        pos = key_match.end()
        while pos < len(text) and text[pos] in " \t\r\n":
            pos += 1
        if pos >= len(text) or text[pos] != "{":
            continue
        start = pos
        depth = 0
        in_string = False
        escape = False
        i = pos
        while i < len(text):
            ch = text[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    spans.append((start, i + 1))
                    break
            i += 1
    return spans


def _ensure_exact_specifier(workspace_dir: str, name: str, version: str) -> bool:
    """Rewrite the package.json specifier for name to an exact pin.

    pnpm's --save-exact does not reliably strip the range operator when
    rewriting an existing specifier — observed leaving ^8.0.0 in place
    after `pnpm add pkg@8.0.0 --save-exact` over a prior ^7 range.

    The rewrite only considers text inside a real dependency-type object
    (see `_dependency_block_spans`), tried in file order, never anywhere
    else in the file — the root package.json has a `scripts` block ahead of
    `devDependencies`, and a bare regex anchored merely at the first
    dependency-type key (without a block end boundary) could still match a
    same-named script, or a same-named package listed in a later, unrelated
    section, instead of the actual specifier.

    Returns:
        True if the specifier was rewritten, meaning the lockfile needs
        a resync via `pnpm install`.
    """
    pkg_path = REPO_ROOT / workspace_dir / "package.json"
    text = pkg_path.read_text()
    pattern = re.compile(rf'("{re.escape(name)}":\s*")([^"]*)(")')
    match = None
    for start, end in _dependency_block_spans(text):
        candidate = pattern.search(text, start, end)
        if candidate is not None:
            match = candidate
            break
    if match is None or match.group(2) == version:
        return False
    replacement = f"{match.group(1)}{version}{match.group(3)}"
    new_text = text[: match.start()] + replacement + text[match.end() :]
    _ = pkg_path.write_text(new_text)
    return True


def _apply_node_package(
    workspace: str, name: str, version: str, errors: list[str]
) -> tuple[bool, str]:
    """Apply a single Node package bump: pnpm add, enforce exact pin, resync, dedupe.

    --ignore-scripts on every pnpm call here: this function's only output is
    pnpm-lock.yaml/package.json bytes that get uploaded as a CI artifact and
    then committed and force-pushed by a separate, higher-privileged job
    (contents: write) — code executing on this disk before that upload could
    tamper with what gets pushed. allowBuilds in pnpm-workspace.yaml already
    restricts lifecycle scripts to `electron`, and nothing check.sh exercises
    (type-checking, lint, vite build) launches Electron or needs its binary,
    so skipping scripts here costs nothing functionally.

    A (False, reason) return is guaranteed to leave the manifest pair —
    package.json and pnpm-lock.yaml — exactly as it found them: the exact-pin
    rewrite (a direct file write, not a pnpm operation) can succeed and then
    have its follow-up lockfile resync fail, which would otherwise leave the
    two files mutually inconsistent for a package that was never actually
    applied — this function snapshots both before touching them and restores
    on any failure path (including an unexpected exception, via the
    try/finally below — not just the checked pnpm/dedupe returncodes).

    It says nothing about node_modules, which `pnpm add`/install/dedupe can
    already have mutated before a later step fails — callers that need
    node_modules to match the restored manifests must call
    `_resync_node_modules()` themselves on a False return (update_node's
    loop does this).

    Returns:
        (True, "") on success, (False, reason) on failure at any step.
    """
    pkg_path = REPO_ROOT / _WORKSPACE_DIR[workspace] / "package.json"
    lock_path = REPO_ROOT / "pnpm-lock.yaml"
    pkg_snap = pkg_path.read_bytes()
    lock_snap = lock_path.read_bytes()
    applied = False

    try:
        result = _run(
            [
                "pnpm",
                "add",
                f"{name}@{version}",
                "--save-exact",
                "--ignore-scripts",
                "--filter",
                workspace,
            ],
            cwd=REPO_ROOT,
        )
        if result.returncode != 0:
            return False, f"pnpm add failed — {result.stderr.strip()}"

        if _ensure_exact_specifier(_WORKSPACE_DIR[workspace], name, version):
            # --no-frozen-lockfile: this resync intentionally updates the
            # lockfile, but pnpm defaults frozen-lockfile to on in CI
            # (CI=true), which rejects any install that would change it.
            resync = _run(
                [
                    "pnpm",
                    "install",
                    "--filter",
                    workspace,
                    "--no-frozen-lockfile",
                    "--ignore-scripts",
                ],
                cwd=REPO_ROOT,
            )
            if resync.returncode != 0:
                # pnpm reports ERR_PNPM_OUTDATED_LOCKFILE etc. on stdout,
                # not stderr.
                detail = resync.stderr.strip() or resync.stdout.strip()
                return False, f"lockfile resync failed after exact-pin fix — {detail}"

        # A direct-dependency bump can leave an older resolution of the same
        # package alive elsewhere in the graph (pulled in transitively by an
        # unrelated consumer) unless the lockfile is deduped afterward — this
        # surfaces as duplicate-type errors in svelte-check/tsc, not as a
        # pnpm error, so it has to be handled here rather than left to the
        # caller.
        dedupe = _run(["pnpm", "dedupe", "--ignore-scripts"], cwd=REPO_ROOT)
        if dedupe.returncode != 0:
            detail = dedupe.stderr.strip() or dedupe.stdout.strip()
            return False, f"dedupe failed — {detail}"

        applied = True
        return True, ""
    except Exception as exc:
        # Broad on purpose: any exception here must still hit the finally
        # below and restore package.json/pnpm-lock.yaml, not just OSError.
        return False, f"unexpected error applying {name} — {exc}"
    finally:
        # Guarded so a restore failure here (e.g. the same disk-full
        # condition that caused the failure being handled) can't raise out
        # of a `finally` block — that would silently replace whatever
        # (False, reason) the try/except above was already returning with
        # an unrelated, uncaught exception, discarding the real diagnostic.
        # Routed through _record_error, not a raw print, so this — the
        # manifests possibly left mutually inconsistent — reaches the
        # committed PR body too, not just the CI log, the same as every
        # other failure class in this file.
        if not applied:
            try:
                _ = pkg_path.write_bytes(pkg_snap)
                _ = lock_path.write_bytes(lock_snap)
            except Exception as restore_exc:
                _record_error(
                    errors,
                    "_apply_node_package: failed to restore"
                    f" package.json/pnpm-lock.yaml for {name} after a failed"
                    f" apply — {restore_exc}",
                )


def update_node(
    cooldown: int,
    workspace: str,
    errors: list[str],
    registry_cache: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[Updated], list[Skipped]]:
    """Check and apply Node dependency updates via pnpm for a given workspace.

    *registry_cache*, when passed, is shared across sibling workspaces by the
    caller so a package outdated in more than one of them (e.g. @types/node
    kept version-aligned across root/frontend/electron) isn't fetched from
    the npm registry once per workspace for identical data.

    *errors* is a caller-owned list mutated in place rather than built
    locally and returned — see update_python's docstring for why.
    """
    print(f"\n=== Node (pnpm) — {workspace} ===")

    # pnpm outdated exits 1 when packages are outdated — capture regardless
    result = _run(
        ["pnpm", "outdated", "--json", "--filter", workspace],
        cwd=REPO_ROOT,
    )
    if result.returncode not in {0, 1}:
        _record_error(
            errors,
            f"pnpm outdated failed (exit {result.returncode}): {result.stderr.strip()}",
        )
        return [], []

    raw = result.stdout.strip()
    if not raw:
        print("  nothing outdated")
        return [], []

    try:
        outdated = _parse_pnpm_outdated(raw)
    except json.JSONDecodeError, StopIteration:
        # Try extracting a JSON block from mixed output
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            print("  [warn] could not parse pnpm outdated output")
            return [], []
        outdated = _parse_pnpm_outdated(m.group())

    updated: list[Updated] = []
    skipped: list[Skipped] = []
    fetch_attempted = 0
    fetch_ok = 0
    candidates = 0
    date_unknown = 0

    items = sorted(outdated.items())
    for i, (name, info) in enumerate(items):
        current: str = cast(str, info.get("current", ""))
        latest: str = cast(str, info.get("latest", ""))
        if not latest or latest == current:
            continue
        print(f"  {name}: {current} -> {latest}")

        fetch_attempted += 1
        data = _npm_registry_data(name, registry_cache)
        if data is None:
            continue
        fetch_ok += 1
        # Only counted once the fetch that would tell us its release date
        # has actually succeeded — matching update_python's ordering, so
        # fetch_ok == 0 implies candidates == 0 in both ecosystems. Counting
        # it earlier (right after pnpm outdated confirms an update exists,
        # before the npm fetch) let a package whose fetch failed outright
        # inflate `candidates` without ever incrementing `date_unknown`,
        # which could silently zero out both escalation checks below in a
        # workspace mixing total-fetch-failures with date-unknown packages.
        candidates += 1
        release_dt = _npm_release_date(data, latest)
        if release_dt is None:
            print("    [warn] release date unavailable, skipping")
            date_unknown += 1
            continue
        if not _age_ok(release_dt, cooldown):
            days_old = (datetime.now(UTC) - release_dt).days
            print(f"    [skip] {days_old}d old (cooldown: {cooldown}d)")
            skipped.append((name, latest, release_dt))
            continue

        ok, err = _apply_node_package(workspace, name, latest, errors)
        if not ok:
            _record_error(errors, f"{name}: {err}")
            # A failed apply can still have mutated node_modules (pnpm add
            # succeeded, a later step didn't) before package.json/pnpm-lock
            # were rolled back — resync so the next package in this loop
            # isn't checked against a tree that no longer matches them. If
            # the resync itself fails, node_modules can no longer be
            # trusted to match the manifests (`_resync_node_modules`'s own
            # docstring) — stop rather than risk applying more bumps on top
            # of a tree that might already be inconsistent with them,
            # mirroring _verify_node's equivalent bisection loop.
            if not _resync_node_modules():
                remaining = len(items) - i - 1
                _record_error(
                    errors,
                    f"node_modules resync failed after {name} failed to apply —"
                    f" aborting; {remaining} package(s) in this workspace"
                    " dropped without being checked",
                )
                break
            continue

        print("    [ok] updated")
        updated.append((name, current, latest, release_dt))

    _record_registry_health(
        errors,
        fetch_attempted=fetch_attempted,
        fetch_ok=fetch_ok,
        candidates=candidates,
        date_unknown=date_unknown,
        registry_label="npm",
    )

    return updated, skipped


def _resync_node_modules() -> bool:
    """Reinstall node_modules to match package.json/pnpm-lock.yaml on disk.

    check.sh frontend resolves its tools (`pnpm exec eslint`, `pnpm exec
    prettier`, `tsc`, `svelte-check`) from node_modules/.bin, not from the
    manifests directly. Restoring package.json/pnpm-lock.yaml bytes after a
    failed verification step brings the source-of-truth files back in line,
    but says nothing about node_modules — this makes node_modules match them
    explicitly, the same `pnpm install --frozen-lockfile` step CI already
    runs before deps_update.py, so a bisection step that follows a bump to
    one of check.sh's own tools (e.g. eslint, itself a root-workspace
    dependency) can't keep failing against a stale installed binary and
    misattribute the block to an unrelated package.

    Returns:
        True on success, False if the reinstall itself failed, or could not
        even be started (e.g. pnpm missing from PATH) — callers must not
        trust node_modules to match the manifests on disk when this returns
        False, since whatever check.sh runs next would be measuring a
        broken environment, not the package under test.
    """
    try:
        result = _run(
            ["pnpm", "install", "--frozen-lockfile", "--ignore-scripts"], cwd=REPO_ROOT
        )
    except Exception as exc:
        # Every call site treats a bool return as the sole failure signal —
        # an unexpected raise here (e.g. pnpm vanishing from PATH mid-run)
        # must not escape as an exception instead, or it would abort the
        # caller's loop mid-iteration rather than letting it react to a
        # plain False the way it already does for a bad pnpm returncode.
        print(f"::error::node_modules resync failed to run — {exc}")
        return False
    if result.returncode != 0:
        # A restored lockfile failing --frozen-lockfile against its own
        # restored package.json would mean the snapshot pair was already
        # inconsistent — surface it rather than silently leaving
        # node_modules stale for the next bisection step.
        # pnpm reports ERR_PNPM_OUTDATED_LOCKFILE etc. on stdout, not stderr
        # (same fallback as _apply_node_package's lockfile resync above).
        detail = result.stderr.strip() or result.stdout.strip()
        print(
            "::error::node_modules resync failed after restoring"
            f" package.json/pnpm-lock.yaml — {_sanitize_reason(detail, limit=2000)}"
        )
        return False
    return True


def _verify_node(
    snapshots: dict[Path, bytes],
    batch: list[tuple[str, Updated]],
    survivors: list[tuple[str, Updated]],
    blocked: list[tuple[str, Blocked]],
    errors: list[str],
) -> None:
    """Verify a batch of Node updates with check.sh frontend; bisect on failure.

    *survivors*, *blocked*, and *errors* are caller-owned lists mutated in
    place, not built locally and returned — a crash partway through
    bisection (caught by _guard) must not discard packages already
    reconfirmed, or already ruled out, before the crash point. Same
    reasoning as update_python/update_node's caller-owned *errors* list.
    *blocked* entries are tagged with their workspace, same as *survivors*
    and update_node's own accounting — a package blocked in more than one
    workspace in the same run must stay distinguishable in the PR body.

    *snapshots* covers every file `_apply_node_package` can touch across all
    workspaces (pnpm-lock.yaml plus each workspace's package.json) — restore
    is a single loop over the dict instead of one write-back per path, so
    adding or removing a workspace can't leave a path out of the rollback.
    It is the same dict object _guard restores from on a crash, not a
    private copy — this function updates it in place to the just-applied
    bytes after every confirmed survivor, so a crash later in the loop
    makes _guard restore to the latest confirmed-good state instead of the
    pre-batch pristine bytes, which would otherwise silently wipe the
    on-disk change for every survivor already confirmed before the crash —
    while *survivors* (deliberately preserved through the crash) keeps
    reporting them as applied. Same reasoning as _verify_python's *snapshot*.

    A failed `_resync_node_modules()` means node_modules no longer reliably
    reflects the manifests just restored — any check.sh result after that
    point would be measuring a broken environment, not the package under
    test. The initial resync (restoring the pre-batch state) failing aborts
    the whole batch. A per-step resync failing (restoring the pre-package
    state before testing the next candidate) still keeps the package just
    isolated — that verdict was measured before the resync ran — but stops
    bisection there instead of testing further candidates against a tree
    that's no longer trustworthy.
    """
    if _run_check(REPO_ROOT, "frontend"):
        survivors.extend(batch)
        return

    print(
        "::warning::check.sh frontend failed on the full batch"
        " — isolating the offending package(s)"
    )
    pristine = dict(snapshots)
    _restore_snapshot(pristine)
    if not _resync_node_modules():
        _record_error(
            errors,
            "node_modules resync failed while restoring the pre-batch state"
            " — aborting bisection, batch discarded",
        )
        return

    for i, (workspace, (name, old, new, release_dt)) in enumerate(batch):
        step_snap = {path: path.read_bytes() for path in snapshots}
        ok, reason = _apply_node_package(workspace, name, new, errors)
        if ok:
            ok = _run_check(REPO_ROOT, "frontend")
            reason = "check.sh frontend failed"
        if ok:
            print(f"    [ok] {name} ({workspace}): confirmed in isolation")
            survivors.append((workspace, (name, old, new, release_dt)))
            for path in snapshots:
                snapshots[path] = path.read_bytes()
            continue

        _restore_snapshot(step_snap)
        print(f"::warning::{name} ({workspace}): blocked — {reason}")
        blocked.append((workspace, (name, new, _sanitize_reason(reason))))
        if not _resync_node_modules():
            remaining = len(batch) - i - 1
            _record_error(
                errors,
                f"node_modules resync failed after isolating {name} — aborting"
                f" bisection; {remaining} package(s) dropped from this batch"
                " without being verified",
            )
            break


# ---------------------------------------------------------------------------
# PR body
# ---------------------------------------------------------------------------


def _pr_body(
    py_up: list[Updated],
    py_sk: list[Skipped],
    py_blocked: list[Blocked],
    py_errors: list[str],
    nd_up: list[Updated],
    nd_sk: list[Skipped],
    nd_blocked: list[Blocked],
    *,
    nd_errors: list[str],
    nd_error_ws: list[str | None],
    nd_ws: list[str],
    nd_blocked_ws: list[str],
    cooldown: int,
) -> str:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    lines = [
        "## Dependency update",
        "",
        f"Auto-generated on {today} — **{cooldown}-day release cooldown** applied.",
        "",
    ]

    def _section(
        title: str,
        up: list[Updated],
        sk: list[Skipped],
        blocked: list[Blocked],
        errors: list[str],
        workspaces: list[str] | None = None,
        error_workspaces: list[str | None] | None = None,
        blocked_workspaces: list[str] | None = None,
    ) -> None:
        lines.append(f"### {title}")
        if up:
            header = ["Package", *(["Workspace"] if workspaces is not None else [])]
            header += ["Old", "New", "Released"]
            lines.append(f"| {' | '.join(header)} |")
            lines.append(f"|{'|'.join('-' * (len(h) + 2) for h in header)}|")
            ws_col: list[str | None] = (
                list(workspaces) if workspaces is not None else [None] * len(up)
            )
            for (name, old, new, release_dt), ws in zip(up, ws_col, strict=True):
                cells = [f"`{name}`", *([f"`{ws}`"] if ws is not None else [])]
                cells += [old, new, release_dt.strftime("%Y-%m-%d")]
                lines.append(f"| {' | '.join(cells)} |")
        else:
            lines.append("_No updates applied._")
        if sk:
            lines.append("")
            eligible = [
                f"`{n}` {v} _(eligible "
                f"{(release_dt + timedelta(days=cooldown)).strftime('%Y-%m-%d')})_"
                for n, v, release_dt in sk
            ]
            lines.append(f"**Skipped — too recent:** {', '.join(eligible)}")
        if blocked:
            lines.append("")
            blocked_ws_col: list[str | None] = (
                list(blocked_workspaces)
                if blocked_workspaces is not None
                else [None] * len(blocked)
            )
            items = [
                f"`{n}` {v} (`{ws}`) — _{reason}_"
                if ws is not None
                else f"`{n}` {v} — _{reason}_"
                for (n, v, reason), ws in zip(blocked, blocked_ws_col, strict=True)
            ]
            lines.append(
                f"**Blocked — broke `check.sh`, excluded from this update:**"
                f" {'; '.join(items)}"
            )
        if errors:
            lines.append("")
            err_ws_col: list[str | None] = (
                list(error_workspaces)
                if error_workspaces is not None
                else [None] * len(errors)
            )
            items = [
                f"`{ws}`: {_sanitize_reason(e)}"
                if ws is not None
                else _sanitize_reason(e)
                for e, ws in zip(errors, err_ws_col, strict=True)
            ]
            lines.append(
                "**Errors — some packages may not have been checked:**"
                f" {'; '.join(items)}"
            )
        lines.append("")

    _section("Python", py_up, py_sk, py_blocked, py_errors)
    _section(
        "Node / pnpm",
        nd_up,
        nd_sk,
        nd_blocked,
        nd_errors,
        workspaces=nd_ws,
        error_workspaces=nd_error_ws,
        blocked_workspaces=nd_blocked_ws,
    )

    lines += [
        "---",
        "",
        "Before merging, verify the gate passed: run `./scripts/check.sh`.",
    ]
    if py_blocked or nd_blocked:
        lines += [
            "",
            "Blocked packages were excluded automatically, not reverted wholesale —"
            " re-run the updater once the underlying incompatibility is resolved"
            " upstream.",
        ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CHANGELOG.md
# ---------------------------------------------------------------------------


def _changelog_bullet(py_up: list[Updated], nd_up: list[Updated]) -> str | None:
    """Build the single rollup line for a run's applied updates, or None if none.

    One line per script run (not one per package) — every dependency update
    needs a CHANGELOG entry per CLAUDE.md's Changelog Hard Constraint, but this
    updater has no CVE awareness (that's the hand-crafted `fix(deps)` path),
    so everything it applies is a routine, non-security bump and belongs under
    `### Changed`, not `### Security`.
    """
    total = len(py_up) + len(nd_up)
    if total == 0:
        return None
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    plural = "package" if total == 1 else "packages"
    return f"- Dependency updates: {total} {plural} ({today})."


def _insert_changelog_entry(text: str, bullet: str) -> str | None:
    """Insert *bullet* as the last line of `### Changed` under `## [Unreleased]`.

    Anchors on the literal `## [Unreleased]` heading and only ever edits
    within that block (up to the next `## [` heading) — never a published
    version section. Creates the `### Changed` subsection if the block
    doesn't have one yet, appending it after any other subsections already
    there. Returns None if the anchor isn't found, so the caller can warn
    and skip instead of guessing at a different insertion point.
    """
    lines = text.split("\n")
    try:
        start = next(
            i for i, line in enumerate(lines) if line.strip() == "## [Unreleased]"
        )
    except StopIteration:
        return None

    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## [")),
        len(lines),
    )
    block = lines[start + 1 : end]

    changed_idx = next(
        (i for i, line in enumerate(block) if line.strip() == "### Changed"), None
    )

    if changed_idx is None:
        while block and block[-1].strip() == "":
            block.pop()
        while block and block[0].strip() == "":
            block.pop(0)
        new_block = [""]
        if block:
            new_block += [*block, ""]
        new_block += ["### Changed", "", bullet, ""]
        block = new_block
    else:
        stop = next(
            (
                i
                for i in range(changed_idx + 1, len(block))
                if block[i].startswith("### ")
            ),
            len(block),
        )
        sub = block[changed_idx + 1 : stop]
        while sub and sub[-1].strip() == "":
            sub.pop()
        if not sub:
            sub.append("")
        sub.append(bullet)
        sub.append("")
        block[changed_idx + 1 : stop] = sub

    lines[start + 1 : end] = block
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the age-gated dependency updater."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "--cooldown",
        type=int,
        default=COOLDOWN_DAYS,
        metavar="DAYS",
        help="minimum age in days (default: 10)",
    )
    _ = parser.add_argument(
        "--pr-body-file",
        type=Path,
        default=None,
        metavar="PATH",
        help="write PR body markdown to this file",
    )
    _ = parser.add_argument(
        "--verify",
        action="store_true",
        default=False,
        help=(
            "verify each ecosystem's batch with check.sh; on failure, isolate and "
            "exclude only the package(s) that broke it instead of reverting everything"
        ),
    )
    args = parser.parse_args()
    cooldown: int = cast(int, args.cooldown)
    pr_body_file: Path | None = cast(Path | None, args.pr_body_file)
    verify: bool = cast(bool, args.verify)

    backend = REPO_ROOT / "backend"
    uv_lock_path = backend / "uv.lock"
    pnpm_lock_path = REPO_ROOT / "pnpm-lock.yaml"

    # Captured unconditionally, regardless of --verify: update_python/
    # update_node mutate their lockfile as they go, so an unhandled
    # exception needs something to restore to even when --verify was never
    # passed. --verify's own meaning (whether _verify_python/_verify_node's
    # check.sh bisection runs at all) is gated separately, below, on the
    # `verify` flag itself — never on these snapshots being present.
    uv_snap: bytes = uv_lock_path.read_bytes()
    node_snap: dict[Path, bytes] = {
        p: p.read_bytes()
        for p in (
            pnpm_lock_path,
            *(REPO_ROOT / d / "package.json" for d in _WORKSPACE_DIR.values()),
        )
    }

    py_errors: list[str] = []
    py_snap = {uv_lock_path: uv_snap}
    py_result = _guard(
        "update_python", py_errors, py_snap, lambda: update_python(cooldown, py_errors)
    )
    py_up, py_sk = py_result if py_result is not None else ([], [])

    nd_sk: list[Skipped] = []
    nd_up_tagged: list[tuple[str, Updated]] = []
    nd_errors_tagged: list[tuple[str, str]] = []
    registry_cache: dict[str, dict[str, Any]] = {}
    for workspace in _WORKSPACE_DIR:
        ws_errors: list[str] = []
        # A fresh snapshot per iteration, not the single global node_snap:
        # pnpm-lock.yaml is shared across every workspace, so restoring the
        # very first pre-run snapshot on this workspace's crash would also
        # wipe out an earlier workspace's already-applied, already-tracked
        # updates still on disk — undoing more than this workspace broke.
        step_snap = {p: p.read_bytes() for p in node_snap}

        def _resync_hook(ws_errors: list[str] = ws_errors) -> None:
            if not _resync_node_modules():
                _record_error(
                    ws_errors,
                    "node_modules resync failed after restoring the pre-crash state",
                )

        result = _guard(
            "update_node",
            ws_errors,
            step_snap,
            lambda ws=workspace, errs=ws_errors: update_node(
                cooldown, ws, errs, registry_cache
            ),
            on_restore=_resync_hook,
        )
        if result is not None:
            up, sk = result
            nd_up_tagged += [(workspace, u) for u in up]
            nd_sk += sk
        nd_errors_tagged += [(workspace, msg) for msg in ws_errors]

    # py_blocked/nd_blocked_tagged are caller-owned: _verify_python/
    # _verify_node mutate them in place as they go, so whatever they already
    # confirmed or ruled out before a mid-batch crash survives even though
    # _guard discards fn()'s own return value on that path.
    py_blocked: list[Blocked] = []
    nd_blocked_tagged: list[tuple[str, Blocked]] = []

    if verify and py_up:
        py_survivors: list[Updated] = []
        # Same dict object passed to _guard and _verify_python: _verify_python
        # updates it in place as bisection confirms survivors, so a crash
        # later in its loop makes _guard restore the latest confirmed-good
        # state rather than the pre-batch pristine bytes captured here.
        py_verify_snap = {uv_lock_path: uv_snap}
        _ = _guard(
            "_verify_python",
            py_errors,
            py_verify_snap,
            lambda: _verify_python(
                backend, uv_lock_path, py_verify_snap, py_up, py_survivors, py_blocked
            ),
        )
        py_up = py_survivors

    nd_verify_errors: list[str] = []
    nd_survivors_tagged: list[tuple[str, Updated]] = []
    if verify and nd_up_tagged:

        def _verify_node_resync_hook() -> None:
            if not _resync_node_modules():
                _record_error(
                    nd_verify_errors,
                    "node_modules resync failed after restoring the pre-crash state",
                )

        _ = _guard(
            "_verify_node",
            nd_verify_errors,
            node_snap,
            lambda: _verify_node(
                node_snap,
                nd_up_tagged,
                nd_survivors_tagged,
                nd_blocked_tagged,
                nd_verify_errors,
            ),
            on_restore=_verify_node_resync_hook,
        )
        nd_up_tagged = nd_survivors_tagged
    nd_up = [u for _, u in nd_up_tagged]
    nd_ws = [w for w, _ in nd_up_tagged]
    nd_blocked = [b for _, b in nd_blocked_tagged]
    nd_blocked_ws = [w for w, _ in nd_blocked_tagged]
    nd_errors: list[str] = [msg for _, msg in nd_errors_tagged]
    nd_error_ws: list[str | None] = [ws for ws, _ in nd_errors_tagged]
    for msg in nd_verify_errors:
        nd_errors.append(msg)
        nd_error_ws.append(None)

    total_up = len(py_up) + len(nd_up)
    total_sk = len(py_sk) + len(nd_sk)
    total_blocked = len(py_blocked) + len(nd_blocked)
    print(
        f"\n==> {total_up} updated, {total_sk} skipped (cooldown),"
        f" {total_blocked} blocked (incompatible)"
    )

    if pr_body_file:
        body = _pr_body(
            py_up,
            py_sk,
            py_blocked,
            py_errors,
            nd_up,
            nd_sk,
            nd_blocked,
            nd_errors=nd_errors,
            nd_error_ws=nd_error_ws,
            nd_ws=nd_ws,
            nd_blocked_ws=nd_blocked_ws,
            cooldown=cooldown,
        )
        _ = pr_body_file.write_text(body)
        print(f"    PR body written to {pr_body_file}")

        bullet = _changelog_bullet(py_up, nd_up)
        if bullet:
            changelog_path = REPO_ROOT / "CHANGELOG.md"
            updated_changelog = _insert_changelog_entry(
                changelog_path.read_text(), bullet
            )
            if updated_changelog is None:
                print(
                    "::warning::CHANGELOG.md: '## [Unreleased]' heading not found"
                    " — skipping entry, add it manually"
                )
            else:
                _ = changelog_path.write_text(updated_changelog)
                print("    CHANGELOG.md updated")

    had_error = bool(py_errors) or bool(nd_errors_tagged) or bool(nd_verify_errors)
    if had_error and total_up == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
