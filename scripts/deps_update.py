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
    python scripts/deps_update.py [--cooldown DAYS] [--pr-body-file PATH]
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
    """Emit a GitHub Actions ::error:: annotation and record msg for the PR body."""
    print(f"::error::{msg}")
    errors.append(msg)


def _restore_snapshot(snapshot: dict[Path, bytes]) -> None:
    """Write every path in *snapshot* back to its captured bytes."""
    for path, data in snapshot.items():
        _ = path.write_bytes(data)


def _guard[T](
    label: str,
    errors: list[str],
    snapshot: dict[Path, bytes] | None,
    fn: Callable[[], T],
) -> T | None:
    """Run fn(); on any exception, restore *snapshot* and record the crash.

    Centralizes what main()'s crash handlers must each do — restore state
    before recording the error, not after or not at all — so a new call
    site can't independently forget the restore half of that pair the way
    three of the four hand-written handlers this replaces once did.

    Returns:
        fn()'s result, or None if it raised.
    """
    try:
        return fn()
    except Exception as exc:
        if snapshot is not None:
            _restore_snapshot(snapshot)
        _record_error(errors, f"{label} crashed unexpectedly — {exc}")
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


def update_python(cooldown: int) -> tuple[list[Updated], list[Skipped], list[str]]:
    """Check and apply Python dependency updates via uv."""
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
    errors: list[str] = []
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

    if fetch_attempted > 0 and fetch_ok == 0:
        _record_error(
            errors, "All PyPI registry fetches failed — no packages could be checked"
        )
    elif candidates > 0 and date_unknown == candidates:
        _record_error(
            errors,
            "All release-date lookups failed for packages with an available"
            " update — cooldown could not be evaluated",
        )

    return updated, skipped, errors


def _verify_python(
    backend: Path, uv_lock_path: Path, snap: bytes, batch: list[Updated]
) -> tuple[list[Updated], list[Blocked]]:
    """Verify a batch of Python updates with check.sh backend; bisect on failure."""
    if _run_check(REPO_ROOT, "backend"):
        return batch, []

    print(
        "::warning::check.sh backend failed on the full batch"
        " — isolating the offending package(s)"
    )
    _restore_snapshot({uv_lock_path: snap})

    survivors: list[Updated] = []
    blocked: list[Blocked] = []
    for name, old, new, release_dt in batch:
        step_snap = uv_lock_path.read_bytes()
        ok, reason = _apply_python_package(name, backend)
        if ok:
            ok = _run_check(REPO_ROOT, "backend")
            reason = "check.sh backend failed"
        if ok:
            print(f"    [ok] {name}: confirmed in isolation")
            survivors.append((name, old, new, release_dt))
        else:
            _restore_snapshot({uv_lock_path: step_snap})
            print(f"::warning::{name}: blocked — {reason}")
            blocked.append((name, new, _sanitize_reason(reason)))
    return survivors, blocked


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


def _ensure_exact_specifier(workspace_dir: str, name: str, version: str) -> bool:
    """Rewrite the package.json specifier for name to an exact pin.

    pnpm's --save-exact does not reliably strip the range operator when
    rewriting an existing specifier — observed leaving ^8.0.0 in place
    after `pnpm add pkg@8.0.0 --save-exact` over a prior ^7 range.

    Returns:
        True if the specifier was rewritten, meaning the lockfile needs
        a resync via `pnpm install`.
    """
    pkg_path = REPO_ROOT / workspace_dir / "package.json"
    text = pkg_path.read_text()
    pattern = re.compile(rf'("{re.escape(name)}":\s*")([^"]*)(")')
    match = pattern.search(text)
    if match is None or match.group(2) == version:
        return False
    new_text = pattern.sub(rf"\g<1>{version}\g<3>", text, count=1)
    _ = pkg_path.write_text(new_text)
    return True


def _apply_node_package(workspace: str, name: str, version: str) -> tuple[bool, str]:
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
        if not applied:
            _ = pkg_path.write_bytes(pkg_snap)
            _ = lock_path.write_bytes(lock_snap)


def update_node(
    cooldown: int,
    workspace: str,
    registry_cache: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[Updated], list[Skipped], list[str]]:
    """Check and apply Node dependency updates via pnpm for a given workspace.

    *registry_cache*, when passed, is shared across sibling workspaces by the
    caller so a package outdated in more than one of them (e.g. @types/node
    kept version-aligned across root/frontend/electron) isn't fetched from
    the npm registry once per workspace for identical data.
    """
    print(f"\n=== Node (pnpm) — {workspace} ===")

    # pnpm outdated exits 1 when packages are outdated — capture regardless
    result = _run(
        ["pnpm", "outdated", "--json", "--filter", workspace],
        cwd=REPO_ROOT,
    )
    if result.returncode not in {0, 1}:
        errs: list[str] = []
        _record_error(
            errs,
            f"pnpm outdated failed (exit {result.returncode}): {result.stderr.strip()}",
        )
        return [], [], errs

    raw = result.stdout.strip()
    if not raw:
        print("  nothing outdated")
        return [], [], []

    try:
        outdated = _parse_pnpm_outdated(raw)
    except json.JSONDecodeError, StopIteration:
        # Try extracting a JSON block from mixed output
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            print("  [warn] could not parse pnpm outdated output")
            return [], [], []
        outdated = _parse_pnpm_outdated(m.group())

    updated: list[Updated] = []
    skipped: list[Skipped] = []
    errors: list[str] = []
    fetch_attempted = 0
    fetch_ok = 0
    candidates = 0
    date_unknown = 0

    for name, info in sorted(outdated.items()):
        current: str = cast(str, info.get("current", ""))
        latest: str = cast(str, info.get("latest", ""))
        if not latest or latest == current:
            continue
        print(f"  {name}: {current} -> {latest}")
        candidates += 1

        fetch_attempted += 1
        data = _npm_registry_data(name, registry_cache)
        if data is None:
            continue
        fetch_ok += 1
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

        ok, err = _apply_node_package(workspace, name, latest)
        if not ok:
            _record_error(errors, f"{name}: {err}")
            # A failed apply can still have mutated node_modules (pnpm add
            # succeeded, a later step didn't) before package.json/pnpm-lock
            # were rolled back — resync so the next package in this loop
            # isn't checked against a tree that no longer matches them.
            if not _resync_node_modules():
                _record_error(
                    errors, f"node_modules resync failed after {name} failed to apply"
                )
            continue

        print("    [ok] updated")
        updated.append((name, current, latest, release_dt))

    if fetch_attempted > 0 and fetch_ok == 0:
        _record_error(
            errors, "All npm registry fetches failed — no packages could be checked"
        )
    elif candidates > 0 and date_unknown == candidates:
        _record_error(
            errors,
            "All release-date lookups failed for packages with an available"
            " update — cooldown could not be evaluated",
        )

    return updated, skipped, errors


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
        True on success, False if the reinstall itself failed — callers
        must not trust node_modules to match the manifests on disk when
        this returns False, since whatever check.sh runs next would be
        measuring a broken environment, not the package under test.
    """
    result = _run(
        ["pnpm", "install", "--frozen-lockfile", "--ignore-scripts"], cwd=REPO_ROOT
    )
    if result.returncode != 0:
        # A restored lockfile failing --frozen-lockfile against its own
        # restored package.json would mean the snapshot pair was already
        # inconsistent — surface it rather than silently leaving
        # node_modules stale for the next bisection step.
        print(
            "::error::node_modules resync failed after restoring"
            f" package.json/pnpm-lock.yaml — {result.stderr.strip()}"
        )
        return False
    return True


def _verify_node(
    snapshots: dict[Path, bytes],
    batch: list[tuple[str, Updated]],
) -> tuple[list[tuple[str, Updated]], list[Blocked], list[str]]:
    """Verify a batch of Node updates with check.sh frontend; bisect on failure.

    *snapshots* covers every file `_apply_node_package` can touch across all
    workspaces (pnpm-lock.yaml plus each workspace's package.json) — restore
    is a single loop over the dict instead of one write-back per path, so
    adding or removing a workspace can't leave a path out of the rollback.

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
        return batch, [], []

    print(
        "::warning::check.sh frontend failed on the full batch"
        " — isolating the offending package(s)"
    )
    _restore_snapshot(snapshots)
    if not _resync_node_modules():
        msg = (
            "node_modules resync failed while restoring the pre-batch state"
            " — aborting bisection, batch discarded"
        )
        return [], [], [msg]

    survivors: list[tuple[str, Updated]] = []
    blocked: list[Blocked] = []
    errors: list[str] = []
    for i, (workspace, (name, old, new, release_dt)) in enumerate(batch):
        step_snap = {path: path.read_bytes() for path in snapshots}
        ok, reason = _apply_node_package(workspace, name, new)
        if ok:
            ok = _run_check(REPO_ROOT, "frontend")
            reason = "check.sh frontend failed"
        if ok:
            print(f"    [ok] {name} ({workspace}): confirmed in isolation")
            survivors.append((workspace, (name, old, new, release_dt)))
            continue

        _restore_snapshot(step_snap)
        print(f"::warning::{name} ({workspace}): blocked — {reason}")
        blocked.append((name, new, _sanitize_reason(reason)))
        if not _resync_node_modules():
            remaining = len(batch) - i - 1
            msg = (
                f"node_modules resync failed after isolating {name} — aborting"
                f" bisection; {remaining} package(s) dropped from this batch"
                " without being verified"
            )
            errors.append(msg)
            break
    return survivors, blocked, errors


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
            items = [f"`{n}` {v} — _{reason}_" for n, v, reason in blocked]
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
        "update_python", py_errors, py_snap, lambda: update_python(cooldown)
    )
    if py_result is None:
        py_up, py_sk = [], []
    else:
        py_up, py_sk, fn_errors = py_result
        py_errors += fn_errors

    nd_sk: list[Skipped] = []
    nd_up_tagged: list[tuple[str, Updated]] = []
    nd_errors_by_ws: dict[str, list[str]] = {}
    registry_cache: dict[str, dict[str, Any]] = {}
    for workspace in _WORKSPACE_DIR:
        ws_errors: list[str] = []
        # A fresh snapshot per iteration, not the single global node_snap:
        # pnpm-lock.yaml is shared across every workspace, so restoring the
        # very first pre-run snapshot on this workspace's crash would also
        # wipe out an earlier workspace's already-applied, already-tracked
        # updates still on disk — undoing more than this workspace broke.
        step_snap = {p: p.read_bytes() for p in node_snap}
        result = _guard(
            "update_node",
            ws_errors,
            step_snap,
            lambda ws=workspace: update_node(cooldown, ws, registry_cache),
        )
        if result is None:
            if not _resync_node_modules():
                _record_error(
                    ws_errors,
                    "node_modules resync failed after restoring the pre-crash state",
                )
            nd_errors_by_ws[workspace] = ws_errors
            continue
        up, sk, errs = result
        nd_up_tagged += [(workspace, u) for u in up]
        nd_sk += sk
        if errs:
            nd_errors_by_ws[workspace] = errs

    py_blocked: list[Blocked] = []
    nd_blocked: list[Blocked] = []

    if verify and py_up:
        verify_result = _guard(
            "_verify_python",
            py_errors,
            {uv_lock_path: uv_snap},
            lambda: _verify_python(backend, uv_lock_path, uv_snap, py_up),
        )
        if verify_result is None:
            py_up = []
        else:
            py_up, py_blocked = verify_result

    nd_verify_errors: list[str] = []
    if verify and nd_up_tagged:
        verify_result = _guard(
            "_verify_node",
            nd_verify_errors,
            node_snap,
            lambda: _verify_node(node_snap, nd_up_tagged),
        )
        if verify_result is None:
            if not _resync_node_modules():
                _record_error(
                    nd_verify_errors,
                    "node_modules resync failed after restoring the pre-crash state",
                )
            nd_up_tagged = []
        else:
            nd_up_tagged, nd_blocked, verify_errors = verify_result
            nd_verify_errors += verify_errors
    nd_up = [u for _, u in nd_up_tagged]
    nd_ws = [w for w, _ in nd_up_tagged]
    nd_errors: list[str] = []
    nd_error_ws: list[str | None] = []
    for ws, msgs in nd_errors_by_ws.items():
        for msg in msgs:
            nd_errors.append(msg)
            nd_error_ws.append(ws)
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

    had_error = bool(py_errors) or bool(nd_errors_by_ws) or bool(nd_verify_errors)
    if had_error and total_up == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
